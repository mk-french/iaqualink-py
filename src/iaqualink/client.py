from __future__ import annotations

import logging
from os import path
from types import TracebackType
from typing import Any, Dict, Optional, Type

import aiohttp
import asyncio

from iaqualink.const import (
    AQUALINK_API_KEY,
    AQUALINK_COMMAND_GET_DEVICES,
    AQUALINK_COMMAND_GET_HOME,
    AQUALINK_DEVICES_URL,
    AQUALINK_LOGIN_URL,
    AQUALINK_SESSION_URL,
    AQUALINK_AWSENDPOINT,
    AQUALINK_AWSMQTTPORT
)
from iaqualink.exception import (
    AqualinkServiceException,
    AqualinkServiceUnauthorizedException,
)
from iaqualink.system import AqualinkSystem
from iaqualink.typing import Payload

from AWSIoTPythonSDK.MQTTLib import AWSIoTMQTTShadowClient
from AWSIoTPythonSDK.MQTTLib import AWSIoTMQTTClient

AQUALINK_HTTP_HEADERS = {
    "user-agent": "okhttp/3.14.7",
    "content-type": "application/json",
}

LOGGER = logging.getLogger("iaqualink")

CERTPATH = path.join(path.dirname(__file__), 'SFSRootCAG2.pem') # might not belong in src but good enough for now...

class AqualinkClient:
    def __init__(
        self,
        username: str,
        password: str,
        session: Optional[aiohttp.ClientSession] = None,
    ):
        self._username = username
        self._password = password
        self._logged = False

        if session is None:
            self._session = None
            self._must_clean_session = True
        else:
            self._session = session
            self._must_clean_session = False

        self._session_id = ""
        self._token = ""
        self._user_id = ""
        self._IdToken = ""

        self._last_refresh = 0

        self.MQTTShadowClient = None
        self.MQTTSystems = set()

    @property
    def logged(self) -> bool:
        return self._logged

    async def close(self) -> None:
        if self._must_clean_session is False or self.closed is True:
            return

        # There shouldn't be a case where this is None but this quietens mypy.
        if self._session is not None:
            await self._session.close()

    @property
    def closed(self) -> bool:
        return self._session is None or self._session.closed is True

    async def __aenter__(self) -> AqualinkClient:
        try:
            await self.login()
            return self
        except AqualinkServiceException:
            await self.close()
            raise

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> Optional[bool]:
        # All Exceptions get re-raised.
        await self.close()
        return exc is None

    async def _send_request(
        self,
        url: str,
        method: str = "get",
        headers: dict = {},
        **kwargs: Optional[Dict[str, Any]],
    ) -> aiohttp.ClientResponse:
        # One-time instantiation if we weren't given a session.
        if self._session is None:
            self._session = aiohttp.ClientSession()
        
        # Add any additional headers supplied
        _headers = dict(AQUALINK_HTTP_HEADERS)
        _headers.update(headers)

        LOGGER.debug(f"-> {method.upper()} {url} {_headers} {kwargs}")
        r = await self._session.request(
            method, url, headers=_headers, **kwargs
        )

        LOGGER.debug(f"<- {r.status} {r.reason} - {url}")

        if r.status == 401:
            m = "Unauthorized Access, check your credentials and try again"
            self._logged = False
            raise AqualinkServiceUnauthorizedException

        if r.status != 200:
            m = f"Unexpected response: {r.status} {r.reason}"
            raise AqualinkServiceException(m)

        return r

    async def _send_login_request(self) -> aiohttp.ClientResponse:
        data = {
            "api_key": AQUALINK_API_KEY,
            "email": self._username,
            "password": self._password,
        }
        return await self._send_request(
            AQUALINK_LOGIN_URL, method="post", json=data
        )

    async def login(self) -> None:
        r = await self._send_login_request()

        data = await r.json()
        self._session_id = data["session_id"]
        self._token = data["authentication_token"]
        self._user_id = data["id"]
        self._IdToken = data["userPoolOAuth"]["IdToken"]
        self._appClientId = data["cognitoPool"]["appClientId"]
        self._access_key_id = data["credentials"]["AccessKeyId"]
        self._secret_access_key = data["credentials"]["SecretKey"]
        self._session_token = data["credentials"]["SessionToken"]

        self._logged = True

    async def _send_systems_request(self) -> aiohttp.ClientResponse:
        params = {
            "api_key": AQUALINK_API_KEY,
            "authentication_token": self._token,
            "user_id": self._user_id,
        }
        params_str = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{AQUALINK_DEVICES_URL}?{params_str}"
        return await self._send_request(url)

    async def get_systems(self) -> Dict[str, AqualinkSystem]:
        try:
            r = await self._send_systems_request()
        except AqualinkServiceException as e:
            if "404" in str(e):
                raise AqualinkServiceUnauthorizedException from e
            raise

        data = await r.json()
        systems = [AqualinkSystem.from_data(self, x) for x in data]
        return {x.serial: x for x in systems if x is not None}

    async def _send_session_request(
        self,
        serial: str,
        command: str,
        params: Optional[Payload] = None,
    ) -> aiohttp.ClientResponse:
        if not params:
            params = {}

        params.update(
            {
                "actionID": "command",
                "command": command,
                "serial": serial,
                "sessionID": self._session_id,
            }
        )
        params_str = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{AQUALINK_SESSION_URL}?{params_str}"
        return await self._send_request(url)

    async def send_home_screen_request(
        self, serial: str
    ) -> aiohttp.ClientResponse:
        r = await self._send_session_request(serial, AQUALINK_COMMAND_GET_HOME)
        return r

    async def send_devices_screen_request(
        self, serial: str
    ) -> aiohttp.ClientResponse:
        r = await self._send_session_request(
            serial, AQUALINK_COMMAND_GET_DEVICES
        )
        return r

    def init_MQTT_client(self, system):
        
        # Register the calling system for any future actions required (eg. disconnection flagging)
        self.MQTTSystems.add(system)

        # If a client already exists nothing to do
        if self.MQTTShadowClient is not None:
            return

        # note the event loop so we can refer to it in threaded callbacks used by
        self.main_event_loop = asyncio.get_running_loop()
        
        # set up the MQTT shadow client
        self.MQTTShadowClient = AWSIoTMQTTClient(self._appClientId, useWebsocket=True)
        self.MQTTShadowClient.configureEndpoint(AQUALINK_AWSENDPOINT, AQUALINK_AWSMQTTPORT)
        self.MQTTShadowClient.configureCredentials(CERTPATH)
        self.MQTTShadowClient.configureIAMCredentials(self._access_key_id, self._secret_access_key, self._session_token)

        # If the client is dissconnected, try re-initialising the client with new tokens
        self.MQTTShadowClient.onOffline = self.MQTT_client_onOffline

        # Time configurations
        self.MQTTShadowClient.configureAutoReconnectBackoffTime(1, 32, 20)
        self.MQTTShadowClient.configureConnectDisconnectTimeout(10)  # 10 sec
        self.MQTTShadowClient.configureMQTTOperationTimeout(10)  # 10 sec

        # Connect to AWS IoT
        self.MQTTShadowClient.connect()

    def MQTT_client_onOffline(self):
        # This function will be called if the MQTT client is disconnected
        LOGGER.debug(f"MQTT Client disconnected...")
        # Flag all the systems as disconnected - if they come back they will be marked online by update() or similar
        for MQTTSystem in self.MQTTSystems:
            MQTTSystem.online = False

        # Likely expired tokens, try get some new ones...
        self.reinit_MQTT_client()

    def reinit_MQTT_client(self):
        LOGGER.debug(f"Getting new tokens!")
        # retrieve new tokens - login() is an async function so run it on the event loop, here we should be on a seperate loopless thread.
        asyncio.run_coroutine_threadsafe(self.login(), self.main_event_loop).result()
        # update the tokens in the MQTT client
        self.MQTTShadowClient.configureIAMCredentials(self._access_key_id, self._secret_access_key, self._session_token)