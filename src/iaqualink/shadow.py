import asyncio
import functools
import json
import threading

import logging

from AWSIoTPythonSDK.exception import AWSIoTExceptions

LOGGER = logging.getLogger("iaqualink")

# Configure logging
logger = logging.getLogger("AWSIoTPythonSDK.core")
logger.setLevel(logging.DEBUG)
streamHandler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
streamHandler.setFormatter(formatter)
logger.addHandler(streamHandler)

class Shadow:
    def __init__(self, system):
        self.system = system
        self.getting = False
        self.updating = False
        self.loop = asyncio.get_event_loop()
        if self.system.aqualink.MQTTShadowClient is not None:
            
            #self.shadow_handler = self.system.aqualink.MQTTShadowClient.createShadowHandlerWithName(self.system.serial, True)
            topic = f"$aws/things/{self.system.serial}/shadow/get/accepted"
            LOGGER.debug(f"Subscribing to {topic}")
            self.system.aqualink.MQTTShadowClient.subscribe(topic, 1, self._callback_get_accepted)
            topic = f"$aws/things/{self.system.serial}/shadow/update/accepted"
            LOGGER.debug(f"Subscribing to {topic}")
            self.system.aqualink.MQTTShadowClient.subscribe(topic, 1, self._callback_update_accepted)
            
        else:
            self.shadow_handler = None
            LOGGER.error("MQTTShadowClient has not been initialised")

    async def get(self):
        self.getting = True
        # request the shadow and register the callback
        #token = self.shadow_handler.shadowGet(callback_with_event, 10)
        topic = f"$aws/things/{self.system.serial}/shadow/get"
        try:
            self.system.aqualink.MQTTShadowClient.publish(topic, "", 1)
        except AWSIoTExceptions.publishTimeoutException as e:
            LOGGER.error(f"Publish timeout... Try reconnect")
            self.system.aqualink.reinit_MQTT_client()
            self.system.aqualink.MQTTShadowClient.connect()

        LOGGER.debug(f"Awaiting event...")
        while self.getting:
            await asyncio.sleep(0.5)
        LOGGER.debug(f"Event set!")

        # Wait for the call back to set the event
        #LOGGER.debug(f"Awaiting Response with token: {token}")
        # Event is concurrent not asynchronous so run it in the executor to not block the loop. 
        # There might be a better way to do this... But this async/concurrent hybrid is killing me and this works.
        #await asyncio.get_running_loop().run_in_executor(None, event.wait, 10)
        #if event.is_set():
        #    LOGGER.debug(f"Event Set")
        #else:
        #    LOGGER.debug(f"Event Timed-out...")

        
    async def update(self, payload):
        self.updating = True
        # Form the JSON String
        json_request_payload = json.dumps({"state": {"desired": payload}})

        # Create an Event to synchronize the event loop and the thread that the callback runs in
        #event = threading.Event()
        #callback_with_event = functools.partial(self._parse_shadow_response_update, event)

        # Prep a response object to handle the request/response
        #token = self.shadow_handler.shadowUpdate(json_request_payload, callback_with_event, 5)

        topic = f"$aws/things/{self.system.serial}/shadow/update"
        self.system.aqualink.MQTTShadowClient.publish(topic, json_request_payload, 1)

        LOGGER.debug(f"Awaiting update event...")
        while self.updating:
            await asyncio.sleep(0.5)
        LOGGER.debug(f"Update event set!")

        # Wait for the call back to set the event
        #LOGGER.debug(f"Awaiting Response with token: {token}")
        # Event is concurrent not asynchronous so run it in the executor to not block the loop. 
        #await asyncio.get_running_loop().run_in_executor(None, event.wait, 10)
        #if event.is_set():
        #    LOGGER.debug(f"Event Set")
        #else:
        #    LOGGER.debug(f"Event Timed-out...")
    
    def _callback_get_accepted(self, client, userdata, message):
        LOGGER.debug("Received a new message: ")
        LOGGER.debug(message.payload)
        LOGGER.debug("from topic: ")
        LOGGER.debug(message.topic)
        LOGGER.debug("--------------\n\n")
        try:
            # load the response JSON into a dictionary
            payloadDict = json.loads(message.payload)
            self.system.parse_shadow_response(payloadDict)
        except json.JSONDecodeError as e:
            LOGGER.debug(f"Failed to load the payload {message.payload}")
            payloadDict = {}

        LOGGER.debug("Setting get_event... ")
        self.getting = False
        
    def _callback_update_accepted(self, client, userdata, message):
        LOGGER.debug("Received a new message: ")
        LOGGER.debug(message.payload)
        LOGGER.debug("from topic: ")
        LOGGER.debug(message.topic)
        LOGGER.debug("--------------\n\n")

        LOGGER.debug("Setting update_event... ")
        self.updating = False

    def _parse_shadow_response_update(self, event, payload, responseStatus, token):
        LOGGER.debug(f"Reponse with Token: {token}")
        # load the response JSON into a dictionary
        payloadDict = json.loads(payload)
        # Log the response status and payload
        LOGGER.debug(f"Response Status: {responseStatus}")
        LOGGER.debug(f"Response: {payloadDict}")
        # set the event to flag response recieved
        LOGGER.debug(f"Setting event...")
        event.set()

    