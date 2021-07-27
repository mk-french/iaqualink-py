from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Dict, Optional

import aiohttp
import asyncio
import threading

from iaqualink.const import (
    AQUALINK_COMMAND_SET_AUX,
    AQUALINK_COMMAND_SET_LIGHT,
    AQUALINK_COMMAND_SET_TEMPS,
)
from iaqualink.device import AqualinkDevice
from iaqualink.device import eXODevice
from iaqualink.device import AqualinkState
from iaqualink.exception import (
    AqualinkServiceException,
    AqualinkSystemOfflineException,
)
from iaqualink.typing import Payload

from iaqualink.shadow import Shadow

if TYPE_CHECKING:
    from iaqualink.client import AqualinkClient


MIN_SECS_TO_REFRESH = 15

LOGGER = logging.getLogger("iaqualink")


class AqualinkSystem:
    def __init__(self, aqualink: AqualinkClient, data: Payload):
        self.aqualink = aqualink
        self.data = data
        self.devices: Dict[str, AqualinkDevice] = {}
        self.has_spa: Optional[bool] = None
        self.temp_unit: Optional[str] = None
        self.last_refresh = 0

        # Semantics here are somewhat odd.
        # True/False are obvious, None means "unknown".
        self.online: Optional[bool] = None

    def __repr__(self) -> str:
        attrs = ["name", "serial", "data"]
        attrs = ["%s=%r" % (i, getattr(self, i)) for i in attrs]
        return f'{self.__class__.__name__}({" ".join(attrs)})'

    @property
    def name(self) -> str:
        return self.data["name"]

    @property
    def serial(self) -> str:
        return self.data["serial_number"]

    @classmethod
    def from_data(
        cls, aqualink: AqualinkClient, data: Payload
    ) -> Optional[AqualinkSystem]:
        SYSTEM_TYPES = {"iaqua": AqualinkPoolSystem, "exo": eXOChlorinator}

        class_ = SYSTEM_TYPES.get(data["device_type"])

        if class_ is None:
            LOGGER.warning(
                f"{data['device_type']} is not a supported system type."
            )
            return None

        return class_(aqualink, data)



class AqualinkPoolSystem(AqualinkSystem):
    async def get_devices(self) -> Dict[str, AqualinkDevice]:
        if not self.devices:
            await self.update()
        return self.devices
    
    async def update(self) -> None:
        # Be nice to Aqualink servers since we rely on polling.
        now = int(time.time())
        delta = now - self.last_refresh
        if delta < MIN_SECS_TO_REFRESH:
            LOGGER.debug(f"Only {delta}s since last refresh.")
            return

        try:
            r1 = await self.aqualink.send_home_screen_request(self.serial)
            r2 = await self.aqualink.send_devices_screen_request(self.serial)
        except AqualinkServiceException:
            self.online = None
            raise

        try:
            await self._parse_home_response(r1)
            await self._parse_devices_response(r2)
        except AqualinkSystemOfflineException:
            self.online = False
            raise

        self.online = True
        self.last_refresh = int(time.time())

    async def _parse_home_response(
        self, response: aiohttp.ClientResponse
    ) -> None:
        data = await response.json()

        LOGGER.debug(f"Home response: {data}")

        if data["home_screen"][0]["status"] == "Offline":
            LOGGER.warning(f"Status for system {self.serial} is Offline.")
            raise AqualinkSystemOfflineException

        self.temp_unit = data["home_screen"][3]["temp_scale"]

        # Make the data a bit flatter.
        devices = {}
        for x in data["home_screen"][4:]:
            name = list(x.keys())[0]
            state = list(x.values())[0]
            attrs = {"name": name, "state": state}
            devices.update({name: attrs})

        for k, v in devices.items():
            if k in self.devices:
                for dk, dv in v.items():
                    self.devices[k].data[dk] = dv
            else:
                self.devices[k] = AqualinkDevice.from_data(self, v)

        # Keep track of the presence of the spa so we know whether temp1 is
        # for the spa or the pool. This is pretty ugly.
        if "spa_set_point" in devices:
            self.has_spa = True
        else:
            self.has_spa = False

    async def _parse_devices_response(
        self, response: aiohttp.ClientResponse
    ) -> None:
        data = await response.json()

        LOGGER.debug(f"Devices response: {data}")

        if data["devices_screen"][0]["status"] == "Offline":
            LOGGER.warning(f"Status for system {self.serial} is Offline.")
            raise AqualinkSystemOfflineException

        # Make the data a bit flatter.
        devices = {}
        for x in data["devices_screen"][3:]:
            aux = list(x.keys())[0]
            attrs = {"aux": aux.replace("aux_", ""), "name": aux}
            for y in list(x.values())[0]:
                attrs.update(y)
            devices.update({aux: attrs})

        for k, v in devices.items():
            if k in self.devices:
                for dk, dv in v.items():
                    self.devices[k].data[dk] = dv
            else:
                self.devices[k] = AqualinkDevice.from_data(self, v)
        
    async def set_pump(self, command: str) -> None:
        r = await self.aqualink._send_session_request(self.serial, command)
        await self._parse_home_response(r)

    async def set_heater(self, command: str) -> None:
        r = await self.aqualink._send_session_request(self.serial, command)
        await self._parse_home_response(r)

    async def set_temps(self, temps: Payload) -> None:
        r = await self.aqualink._send_session_request(
            self.serial, AQUALINK_COMMAND_SET_TEMPS, temps
        )
        await self._parse_home_response(r)

    async def set_aux(self, aux: str) -> None:
        aux = AQUALINK_COMMAND_SET_AUX + "_" + aux.replace("aux_", "")
        r = await self.aqualink._send_session_request(self.serial, aux)
        await self._parse_devices_response(r)

    async def set_light(self, data: Payload) -> None:
        r = await self.aqualink._send_session_request(
            self.serial, AQUALINK_COMMAND_SET_LIGHT, data
        )
        await self._parse_devices_response(r)
    

class eXOChlorinator(AqualinkSystem):
    def __init__(self, aqualink: AqualinkClient, data: Payload):
        super().__init__(aqualink, data)

        credentials = {'access_key_id': self.aqualink._access_key_id,
                        'secret_access_key': self.aqualink._secret_access_key,
                        'session_token': self.aqualink._session_token,
                        'signing_region': self.aqualink._signing_region,
                        'client_id': self.aqualink._appClientId+"_py"}
        # create a AWS IOT shadow client
        self.shadow = Shadow(end_point=self.aqualink._end_point, thing_name=self.serial, credentials=credentials)

    async def get_devices(self) -> Dict[str, AqualinkDevice]:
        #if there are no devices, need to create them from the equipment/swc_0 property
        if not self.devices:
            #connect the shadow client
            await asyncio.to_thread(self.shadow.connect) #shadow is not asyncio so send connect to a new thread to avoid blocking
            #TODO: alternatively shadow can become async and we can asyncio.wrap_future()
            
            while not self.shadow.inital_state: #wait for initial state
                print("Waiting for initial state...")
                await asyncio.sleep(1)
                #TODO: build a timeout here for failures
            
            # Process Chlorinator Properties
            equipment = self.shadow.reported['equipment']['swc_0']
            for name, data in equipment.items():
                device = eXODevice.from_data(self, name, data)
                if device: #if a device was created - this is how undesired properties of swc_0 are ignored
                    self.devices[name] = device
            
            # Process Others
            equipment = self.shadow.reported
            for name, data in equipment.items():
                device = eXODevice.from_data(self, name, data)
                if device: 
                    self.devices[name] = device
        return self.devices

    async def update(self) -> None:
        #TODO: this is doing nothing.... remove or revise
        now = int(time.time())
        delta = now - self.last_refresh
        if delta < MIN_SECS_TO_REFRESH:
            LOGGER.debug(f"Only {delta}s since last refresh.")
            return

        self.online = True
        self.last_refresh = int(time.time())
        

    async def set_pump(self, command: str) -> None:
        raise NotImplementedError()

    async def set_heater(self, state: int) -> None:
        desired_state = {"heating":{"enabled":state}}
        self.shadow.change_shadow_state(desired_state)

    async def set_temps(self, temps: Payload) -> None:
        raise NotImplementedError()

    async def set_aux(self, aux: str) -> None:
        raise NotImplementedError()

    async def set_light(self, data: Payload) -> None:
        raise NotImplementedError()