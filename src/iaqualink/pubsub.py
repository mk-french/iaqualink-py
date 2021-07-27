# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0.

import argparse
from awscrt import io, mqtt, auth, http
from awsiot import mqtt_connection_builder
import sys
import time

io.init_logging(getattr(io.LogLevel, io.LogLevel.NoLogs.name), 'stderr')

# Callback when connection is accidentally lost.
def on_connection_interrupted(connection, error, **kwargs):
    print("Connection interrupted. error: {}".format(error))


# Callback when an interrupted connection is re-established.
def on_connection_resumed(connection, return_code, session_present, **kwargs):
    print("Connection resumed. return_code: {} session_present: {}".format(return_code, session_present))

    if return_code == mqtt.ConnectReturnCode.ACCEPTED and not session_present:
        print("Session did not persist. Resubscribing to existing topics...")
        resubscribe_future, _ = connection.resubscribe_existing_topics()

        # Cannot synchronously wait for resubscribe result because we're on the connection's event-loop thread,
        # evaluate result with a callback instead.
        resubscribe_future.add_done_callback(on_resubscribe_complete)


def on_resubscribe_complete(resubscribe_future):
        resubscribe_results = resubscribe_future.result()
        print("Resubscribe results: {}".format(resubscribe_results))

        for topic, qos in resubscribe_results['topics']:
            if qos is None:
                sys.exit("Server rejected resubscribe to topic: {}".format(topic))


# Callback when the subscribed topic receives a message
def on_message_received(topic, payload, dup, qos, retain, **kwargs):
    print("Received message from topic '{}': {}".format(topic, payload))


class PubSub: 
    def __init__(self, end_point, thing_name, credentials):
        self.end_point = end_point
        self.thing_name = thing_name
        self.credentials = credentials

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
            on_connection_interrupted=on_connection_interrupted,
            on_connection_resumed=on_connection_resumed,
            websocket_proxy_options=None,
            client_id=self.credentials['client_id'],
            clean_session=False,
            keep_alive_secs=6)

    
    def connect(self):
        print("Connecting to {} with client ID '{}'...".format(
            self.end_point, self.credentials['client_id']))

        connect_future = self.mqtt_connection.connect()
        # Future.result() waits until a result is available
        connect_future.result()
        print("Connected!")


    def subscribe(self, topic):
        # Subscribe
        print("Subscribing to topic '{}'...".format(topic))
        subscribe_future, packet_id = self.mqtt_connection.subscribe(
            topic=topic,
            qos=mqtt.QoS.AT_LEAST_ONCE,
            callback=on_message_received)

        subscribe_result = subscribe_future.result()
        print("Subscribed with {}".format(str(subscribe_result['qos'])))

    def publish(self, topic, message):
            self.mqtt_connection.publish(
                topic=topic,
                payload=message,
                qos=mqtt.QoS.AT_LEAST_ONCE)


    # Disconnect
    #print("Disconnecting...")
    #disconnect_future = mqtt_connection.disconnect()
    #disconnect_future.result()
    #print("Disconnected!")
