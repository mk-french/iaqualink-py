from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Dict, Optional

import aiohttp

from iaqualink.const import (
    AQUALINK_COMMAND_SET_AUX,
    AQUALINK_COMMAND_SET_LIGHT,
    AQUALINK_COMMAND_SET_TEMPS,
)
from iaqualink.device import AqualinkDevice
from iaqualink.exception import (
    AqualinkServiceException,
    AqualinkServiceUnauthorizedException,
    AqualinkSystemOfflineException,
)
from iaqualink.typing import Payload

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
        SYSTEM_TYPES = {"iaqua": AqualinkPoolSystem, "exo": eXOChlorinator, "zs500": zs500Heater}

        class_ = SYSTEM_TYPES.get(data["device_type"])

        if class_ is None:
            LOGGER.warning(
                f"{data['device_type']} is not a supported system type."
            )
            return None

        return class_(aqualink, data)

    async def get_devices(self) -> Dict[str, AqualinkDevice]:
        if not self.devices:
            await self.update()
        return self.devices

class AqualinkPoolSystem(AqualinkSystem):
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

        self.temp_unit = "C" #TODO: check if unit can be changed on panel?

    async def update(self) -> None:
        # Be nice to Aqualink servers since we rely on polling.
        now = int(time.time())
        delta = now - self.last_refresh
        if delta < MIN_SECS_TO_REFRESH:
            LOGGER.debug(f"Only {delta}s since last refresh.")
            return

        try:
            r1 = await self.aqualink.send_shadow_request(self.serial)
        # catch if a new AWS token is required
        except AqualinkServiceUnauthorizedException:
            try:
                await self.aqualink.login()
                r1 = await self.aqualink.send_shadow_request(self.serial)
            except AqualinkServiceException:
                self.online = None
                raise
        except AqualinkServiceException:
            self.online = None
            raise


        try:
            await self._parse_shadow_response(r1)
        except AqualinkSystemOfflineException:
            self.online = False
            raise

        self.online = True
        self.last_refresh = int(time.time())
    
    async def _parse_shadow_response(
        self, response: aiohttp.ClientResponse
    ) -> None:
        data = await response.json()

        LOGGER.debug(f"Shadow response: {data}")

        # Process the chlorinator attributes
        # Make the data a bit flatter.
        devices = {}
        for name,state in data["state"]["reported"]["equipment"]["swc_0"].items():
            attrs = {"name": name, "state": state}
            try:
                attrs.update(state)
            except:
                pass
            devices.update({name: attrs})
        devices.pop("vsp_speed", None) # temp remove until can handle dictionary

        # Process the heating control attributes
        name = "heating"
        attrs = {"name": name}
        attrs.update(data["state"]["reported"]["heating"])
        devices.update({name: attrs})

        LOGGER.debug(f"devices: {devices}")

        for k, v in devices.items():
            if k in self.devices:
                for dk, dv in v.items():
                    self.devices[k].data[dk] = dv
            else:
                self.devices[k] = AqualinkDevice.from_data(self, v)

    async def set_heater(self, state) -> None:
        r = await self.aqualink.send_shadow_desired(self.serial, {"heating": {"enabled": state}})
        r.raise_for_status()

    async def set_temps(self, sp) -> None:
        r = await self.aqualink.send_shadow_desired(self.serial, {"heating": {"sp": sp}})
        r.raise_for_status()

    async def set_aux(self, aux, state) -> None:
        r = await self.aqualink.send_shadow_desired(self.serial, {"equipment": {"swc_0": {aux: {"state": state}}}})
        r.raise_for_status()
    
    async def set_production(self, state) -> None:
        r = await self.aqualink.send_shadow_desired(self.serial, {"equipment": {"swc_0": {"production": state}}})
        r.raise_for_status()
    
    async def set_boost(self, state) -> None:
        r = await self.aqualink.send_shadow_desired(self.serial, {"equipment": {"swc_0": {"boost": state}}})
        r.raise_for_status()
    
    async def set_low(self, state) -> None:
        r = await self.aqualink.send_shadow_desired(self.serial, {"equipment": {"swc_0": {"low": state}}})
        r.raise_for_status()

class zs500Heater(AqualinkSystem):
    def __init__(self, aqualink: AqualinkClient, data: Payload):
        super().__init__(aqualink, data)

        self.temp_unit = "C" #TODO: check if unit can be changed on panel?

    async def update(self) -> None:
        # Be nice to Aqualink servers since we rely on polling.
        now = int(time.time())
        delta = now - self.last_refresh
        if delta < MIN_SECS_TO_REFRESH:
            LOGGER.debug(f"Only {delta}s since last refresh.")
            return

        try:
            r1 = await self.aqualink.send_shadow_request(self.serial)
        # catch if a new AWS token is required
        except AqualinkServiceUnauthorizedException:
            try:
                await self.aqualink.login()
                r1 = await self.aqualink.send_shadow_request(self.serial)
            except AqualinkServiceException:
                self.online = None
                raise
        except AqualinkServiceException:
            self.online = None
            raise


        try:
            await self._parse_shadow_response(r1)
        except AqualinkSystemOfflineException:
            self.online = False
            raise

        self.online = True
        self.last_refresh = int(time.time())
    
    async def _parse_shadow_response(
        self, response: aiohttp.ClientResponse
    ) -> None:
        data = await response.json()

        LOGGER.debug(f"Shadow response: {data}")

        # Process the heater attributes
        # Make the data a bit flatter.
        devices = {}
        for name,state in data["state"]["reported"]["equipment"]["hp_0"].items():
            attrs = {"name": name, "state": state}
            try:
                attrs.update(state)
            except:
                pass
            devices.update({name: attrs})

        devices.pop("debug", None) # temp remove until can handle dictionary

        LOGGER.debug(f"devices: {devices}")

        for k, v in devices.items():
            if k in self.devices:
                for dk, dv in v.items():
                    self.devices[k].data[dk] = dv
            else:
                self.devices[k] = AqualinkDevice.from_data(self, v)

    async def set_heater(self, state) -> None:
        r = await self.aqualink.send_shadow_desired(self.serial, {"equipment": {"hp_0": {"state": state}}})
        r.raise_for_status()

    async def set_temps(self, sp) -> None:
        r = await self.aqualink.send_shadow_desired(self.serial, {"equipment": {"hp_0": {"tsp": sp}}})
        r.raise_for_status()