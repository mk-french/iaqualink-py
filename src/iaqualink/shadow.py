import asyncio
import functools
import json
import threading

import logging

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

        if self.system.aqualink.MQTTShadowClient is not None:
            self.shadow_handler = self.system.aqualink.MQTTShadowClient.createShadowHandlerWithName(self.system.serial, True)
        else:
            self.shadow_handler = None
            LOGGER.error("MQTTShadowClient has not been initialised")

    async def get(self):
        # Create an Event to synchronize the event loop and the thread that the callback runs in
        event = threading.Event()
        # Tack the event onto the callback= function
        callback_with_event = functools.partial(self._parse_shadow_response, event)

        # request the shadow and register the callback
        token = self.shadow_handler.shadowGet(callback_with_event, 10)
        
        # Wait for the call back to set the event
        LOGGER.debug(f"Awaiting Response with token: {token}")
        # Event is concurrent not asynchronous so run it in the executor to not block the loop. 
        # There might be a better way to do this... But this async/concurrent hybrid is killing me and this works.
        await asyncio.get_running_loop().run_in_executor(None, event.wait)
        
    async def update(self, payload):
        # Form the JSON String
        json_request_payload = json.dumps({"state": {"desired": payload}})

        # Create an Event to synchronize the event loop and the thread that the callback runs in
        event = threading.Event()
        callback_with_event = functools.partial(self._parse_shadow_response_update, event)

        # Prep a response object to handle the request/response
        token = self.shadow_handler.shadowUpdate(json_request_payload, callback_with_event, 5)

        # Wait for the call back to set the event
        LOGGER.debug(f"Awaiting Response with token: {token}")
        # Event is concurrent not asynchronous so run it in the executor to not block the loop. 
        await asyncio.get_running_loop().run_in_executor(None, event.wait)

    def _parse_shadow_response(self, event, payload, responseStatus, token):
        LOGGER.debug(f"Reponse with Token: {token}")
        # load the response JSON into a dictionary
        payloadDict = json.loads(payload)
        # check the response status
        LOGGER.debug(f"Response Status: {responseStatus}")
        if responseStatus == 'accepted':
            # hand off the rest of the parsing to the system for system specific unpacking
            self.system.parse_shadow_response(payloadDict)
        else:
            LOGGER.debug(f"Response: {payloadDict}")
        # set the event to flag response recieved
        event.set()

    def _parse_shadow_response_update(self, event, payload, responseStatus, token):
        LOGGER.debug(f"Reponse with Token: {token}")
        # load the response JSON into a dictionary
        payloadDict = json.loads(payload)
        # Log the response status and payload
        LOGGER.debug(f"Response Status: {responseStatus}")
        LOGGER.debug(f"Response: {payloadDict}")
        # set the event to flag response recieved
        event.set()