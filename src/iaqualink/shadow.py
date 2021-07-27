# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0.


from awscrt import auth, io, mqtt, http
from awsiot import iotshadow
from awsiot import mqtt_connection_builder
from concurrent.futures import Future
import sys
import threading
import asyncio
import traceback
from uuid import uuid4

import time
import collections.abc

# - Overview -
# This sample uses the AWS IoT Device Shadow Service to keep a property in
# sync between device and server. Imagine a light whose color may be changed
# through an app, or set by a local user.
#
# - Instructions -
# Once connected, type a value in the terminal and press Enter to update
# the property's "reported" value. The sample also responds when the "desired"
# value changes on the server. To observe this, edit the Shadow document in
# the AWS Console and set a new "desired" value.
#
# - Detail -
# On startup, the sample requests the shadow document to learn the property's
# initial state. The sample also subscribes to "delta" events from the server,
# which are sent when a property's "desired" value differs from its "reported"
# value. When the sample learns of a new desired value, that value is changed
# on the device and an update is sent to the server with the new "reported"
# value.

class LockedData:
    def __init__(self):
        self.lock = threading.Lock()
        self.value = None
        self.metadata = None #TODO: store timestamps
        self.version = None #TODO: store versions

    def __repr__(self) -> str:
        attrs = ["value", "metadata"]
        attrs = ["%s=%r" % (i, getattr(self, i)) for i in attrs]
        return f'{self.__class__.__name__}({", ".join(attrs)})'
    
    def set_value(self, value, version=None, metadata=None):
        with self.lock:
            #print(f'  Setting value to: {value}')
            self.value = value
            self.metadata = metadata
            self.version = version

class Shadow:
    def __init__(self, end_point, thing_name, credentials):
        self.end_point = end_point
        self.thing_name = thing_name
        self.credentials = credentials

        self.mqtt_connection = None
        self.shadow_client = None

        self.desired = {}
        self.reported = {}

        self.inital_state = False
    
    @staticmethod
    def update(d, u, version=None, metadata=None):
        ''' This function updates the local dictionary with values from the shadow response.
            The dictionary can be hierarchical and all elements are not guranteed in every response.
            Thus, doing it recursively and only updating the final elements avoids data loss.
            Note, final elements are wrapped in an instance of LockedData.
        '''
        for k, v in u.items():
            #print(f"Processing: {k}")
            if isinstance(v, collections.abc.Mapping):
                d[k] = Shadow.update(d.get(k, {}), v, version, metadata.get(k, {}) if metadata is not None else None)
            else:
                if k not in d:
                    d[k] = LockedData()
                d[k].set_value(v, version, metadata.get(k, {}) if metadata is not None else None)
        return d

    # Function for gracefully quitting this sample
    def exit(msg_or_exception): #TODO: update this
        if isinstance(msg_or_exception, Exception):
            print("Exiting sample due to exception.")
            traceback.print_exception(msg_or_exception.__class__, msg_or_exception, sys.exc_info()[2])
        else:
            print("Exiting sample:", msg_or_exception)

        with self.locked_data.lock:
            if not self.locked_data.disconnect_called:
                print("Disconnecting...")
                self.locked_data.disconnect_called = True
                future = mqtt_connection.disconnect()
                future.add_done_callback(on_disconnected)
    
    def on_disconnected(self, disconnect_future):
        # type: (Future) -> None
        print("Disconnected.")


    def on_get_shadow_accepted(self,response):
        # type: (iotshadow.GetShadowResponse) -> None
        try:
            print("Finished getting initial shadow state. (on_get_shadow_accepted)")
            #print("  Response contains '{}'".format(response))
            if response.state:
                if response.state.delta:
                    print("Update Desired")
                    Shadow.update(self.desired, response.state.delta, metadata=response.metadata.desired)
                    
                if response.state.reported:
                    print("Update Reported")
                    #print(threading.get_ident())
                    Shadow.update(self.reported, response.state.reported, metadata=response.metadata.reported)
            self.inital_state = True #flag that the initial state is loaded
            return

        except Exception as e:
            exit(e)

    def on_get_shadow_rejected(self, error):
        # type: (iotshadow.ErrorResponse) -> None
        if error.code == 404:
            print("Thing has no shadow document. ")
        else:
            exit("Get request was rejected. code:{} message:'{}'".format(
                error.code, error.message))

    def on_shadow_delta_updated(self, delta):
        # type: (iotshadow.ShadowDeltaUpdatedEvent) -> None
        try:
            print("Received shadow delta event. (on_shadow_delta_updated)")
            print("  Delta contains '{}'".format(delta))
            if delta.state:
                print("Update Desired")
                Shadow.update(self.desired, delta.state)
            else:
                print("  Delta did not report a change")

        except Exception as e:
            exit(e)

    def on_publish_update_shadow(self, future):
        #type: (Future) -> None
        try:
            future.result()
            print("Update request published.")
        except Exception as e:
            print("Failed to publish update request.")
            exit(e)

    def on_update_shadow_accepted(self, response):
        # type: (iotshadow.UpdateShadowResponse) -> None
        try:
            print("Received shadow update event. (on_update_shadow_accepted)")
            print("  Update contains '{}'".format(response))
            if response.state:
                if response.state.desired:
                    print("Update Desired")
                    Shadow.update(self.desired, response.state.desired)
                
                if response.state.reported:
                    print("Update Reported")
                    Shadow.update(self.reported, response.state.reported)

            return

        except Exception as e:
            exit(e)

    def on_update_shadow_rejected(self, error):
        # type: (iotshadow.ErrorResponse) -> None
        exit("Update request was rejected. code:{} message:'{}'".format(
            error.code, error.message))
    
    def change_shadow_state(self, desired_state):
        print("Updating desired shadow properties...")

        request = iotshadow.UpdateShadowRequest(
            thing_name=self.thing_name,
            state=iotshadow.ShadowState(
                desired=desired_state,
            )
        )
        future = self.shadow_client.publish_update_shadow(request, mqtt.QoS.AT_LEAST_ONCE)
        future.add_done_callback(self.on_publish_update_shadow)

    def connect(self):
        io.init_logging(getattr(io.LogLevel, io.LogLevel.NoLogs.name), 'stderr')

        # Spin up resources
        event_loop_group = io.EventLoopGroup(1)
        host_resolver = io.DefaultHostResolver(event_loop_group)
        client_bootstrap = io.ClientBootstrap(event_loop_group, host_resolver)

        # Set up credentials
        credentials_provider = auth.AwsCredentialsProvider.new_static(
            access_key_id = self.credentials['access_key_id'],
            secret_access_key = self.credentials['secret_access_key'],
            session_token = self.credentials['session_token']
        )
        # Define the connection
        self.mqtt_connection = mqtt_connection_builder.websockets_with_default_aws_signing(
            endpoint=self.end_point,
            client_bootstrap=client_bootstrap,
            region=self.credentials['signing_region'],
            credentials_provider=credentials_provider,
            websocket_proxy_options=None,
            client_id=self.credentials['client_id'],
            clean_session=False,
            keep_alive_secs=6)


        #print(threading.get_ident())
        print("Connecting to {} with client ID '{}'...".format(
            self.end_point, self.credentials['client_id']))

        connected_future = self.mqtt_connection.connect()

        self.shadow_client = iotshadow.IotShadowClient(self.mqtt_connection)

        # Wait for connection to be fully established.
        # Note that it's not necessary to wait, commands issued to the
        # mqtt_connection before its fully connected will simply be queued.
        # But this sample waits here so it's obvious when a connection
        # fails or succeeds.
        connected_future.result()
        print("Connected!")

        try:
            # Subscribe to necessary topics.
            # Note that is **is** important to wait for "accepted/rejected" subscriptions
            # to succeed before publishing the corresponding "request".
            print("Subscribing to Delta events...")
            delta_subscribed_future, _ = self.shadow_client.subscribe_to_shadow_delta_updated_events(
                request=iotshadow.ShadowDeltaUpdatedSubscriptionRequest(thing_name=self.thing_name),
                qos=mqtt.QoS.AT_LEAST_ONCE,
                callback=self.on_shadow_delta_updated)

            # Wait for subscription to succeed
            delta_subscribed_future.result()

            print("Subscribing to Update responses...")
            update_accepted_subscribed_future, _ = self.shadow_client.subscribe_to_update_shadow_accepted(
                request=iotshadow.UpdateShadowSubscriptionRequest(thing_name=self.thing_name),
                qos=mqtt.QoS.AT_LEAST_ONCE,
                callback=self.on_update_shadow_accepted)

            update_rejected_subscribed_future, _ = self.shadow_client.subscribe_to_update_shadow_rejected(
                request=iotshadow.UpdateShadowSubscriptionRequest(thing_name=self.thing_name),
                qos=mqtt.QoS.AT_LEAST_ONCE,
                callback=self.on_update_shadow_rejected)

            # Wait for subscriptions to succeed
            update_accepted_subscribed_future.result()
            update_rejected_subscribed_future.result()

            print("Subscribing to Get responses...")
            get_accepted_subscribed_future, _ = self.shadow_client.subscribe_to_get_shadow_accepted(
                request=iotshadow.GetShadowSubscriptionRequest(thing_name=self.thing_name),
                qos=mqtt.QoS.AT_LEAST_ONCE,
                callback=self.on_get_shadow_accepted)

            get_rejected_subscribed_future, _ = self.shadow_client.subscribe_to_get_shadow_rejected(
                request=iotshadow.GetShadowSubscriptionRequest(thing_name=self.thing_name),
                qos=mqtt.QoS.AT_LEAST_ONCE,
                callback=self.on_get_shadow_rejected)

            # Wait for subscriptions to succeed
            get_accepted_subscribed_future.result()
            get_rejected_subscribed_future.result()

            # The rest of the sample runs asyncronously.

            # Issue request for shadow's current state.
            # The response will be received by the on_get_accepted() callback
            print("Requesting current shadow state...")
            publish_get_future = self.shadow_client.publish_get_shadow(
                request=iotshadow.GetShadowRequest(thing_name=self.thing_name),
                qos=mqtt.QoS.AT_LEAST_ONCE)

            # Ensure that publish succeeds
            publish_get_future.result()

        except Exception as e:
            exit(e)