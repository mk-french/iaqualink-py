from __future__ import annotations

import unittest
from unittest.mock import MagicMock

import pytest

from iaqualink.device import (
    AqualinkAuxToggle,
    AqualinkBinarySensor,
    AqualinkSensor,
    eXOLow,
    eXOAuxToggle,
    eXOBoost,
    eXOProduction,
    eXOSensor,
    eXOThermostat
    )
from iaqualink.exception import (
    AqualinkServiceException,
    AqualinkSystemOfflineException,
)
from iaqualink.system import AqualinkPoolSystem, AqualinkSystem, eXOChlorinator, zs500Heater

from .common import async_noop, async_raises, async_returns


class TestAqualinkSystem(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        pass

    def test_from_data_iaqua(self):
        aqualink = MagicMock()
        data = {"id": 1, "serial_number": "ABCDEFG", "device_type": "iaqua"}
        r = AqualinkSystem.from_data(aqualink, data)
        assert r is not None
        assert isinstance(r, AqualinkPoolSystem)

    def test_from_data_exo(self):
        aqualink = MagicMock()
        data = {"id": 1, "serial_number": "ABCDEFG", "device_type": "exo"}
        r = AqualinkSystem.from_data(aqualink, data)
        assert r is not None
        assert isinstance(r, eXOChlorinator)

    def test_from_data_zs500(self):
        aqualink = MagicMock()
        data = {"id": 1, "serial_number": "ABCDEFG", "device_type": "zs500"}
        r = AqualinkSystem.from_data(aqualink, data)
        assert r is not None
        assert isinstance(r, zs500Heater)

    def test_from_data_unsupported(self):
        aqualink = MagicMock()
        data = {"id": 1, "serial_number": "ABCDEFG", "device_type": "foo"}
        r = AqualinkSystem.from_data(aqualink, data)
        assert r is None

    async def test_update_success(self):
        aqualink = MagicMock()
        data = {"id": 1, "serial_number": "ABCDEFG", "device_type": "iaqua"}
        r = AqualinkSystem.from_data(aqualink, data)
        r.aqualink.send_home_screen_request = async_noop
        r.aqualink.send_devices_screen_request = async_noop
        r._parse_home_response = async_noop
        r._parse_devices_response = async_noop
        await r.update()
        assert r.online is True

    async def test_update_service_exception(self):
        aqualink = MagicMock()
        data = {"id": 1, "serial_number": "ABCDEFG", "device_type": "iaqua"}
        r = AqualinkSystem.from_data(aqualink, data)
        r.aqualink.send_home_screen_request = async_raises(
            AqualinkServiceException
        )
        with pytest.raises(AqualinkServiceException):
            await r.update()
        assert r.online is None

    async def test_update_offline(self):
        aqualink = MagicMock()
        data = {"id": 1, "serial_number": "ABCDEFG", "device_type": "iaqua"}
        r = AqualinkSystem.from_data(aqualink, data)
        r.aqualink.send_home_screen_request = async_noop
        r.aqualink.send_devices_screen_request = async_noop
        r._parse_home_response = async_raises(AqualinkSystemOfflineException)

        with pytest.raises(AqualinkSystemOfflineException):
            await r.update()
        assert r.online is False

    async def test_parse_devices_offline(self):
        data = {"id": 1, "serial_number": "ABCDEFG", "device_type": "iaqua"}
        aqualink = MagicMock()
        system = AqualinkSystem.from_data(aqualink, data)

        message = {"message": "", "devices_screen": [{"status": "Offline"}]}
        response = MagicMock()
        response.json = async_returns(message)

        with pytest.raises(AqualinkSystemOfflineException):
            await system._parse_devices_response(response)
        assert system.devices == {}

    async def test_parse_devices_good(self):
        data = {"id": 1, "serial_number": "ABCDEFG", "device_type": "iaqua"}
        aqualink = MagicMock()
        system = AqualinkSystem.from_data(aqualink, data)

        message = {
            "message": "",
            "devices_screen": [
                {"status": "Online"},
                {"response": ""},
                {"group": "1"},
                {
                    "aux_B1": [
                        {"state": "0"},
                        {"label": "Label B1"},
                        {"icon": "aux_1_0.png"},
                        {"type": "0"},
                        {"subtype": "0"},
                    ]
                },
            ],
        }
        response = MagicMock()
        response.json = async_returns(message)

        expected = {
            "aux_B1": AqualinkAuxToggle(
                system=system,
                data={
                    "aux": "B1",
                    "name": "aux_B1",
                    "state": "0",
                    "label": "Label B1",
                    "icon": "aux_1_0.png",
                    "type": "0",
                    "subtype": "0",
                },
            )
        }
        await system._parse_devices_response(response)
        assert system.devices == expected

    async def test_parse_devices_exo_good(self):
        data = {"id": 1, "serial_number": "ABCDEFG", "device_type": "exo"}
        aqualink = MagicMock()
        system = AqualinkSystem.from_data(aqualink, data)

        message = {
            "state": {
                "reported": {
                    "vr": "V85W4",
                    "color": "off",
                    "heating": {
                        "sp": 32,
                        "state": 0,
                        "sp_min": 15,
                        "sp_max": 32,
                        "enabled": 1,
                        "vsp_rpm_list": {
                            "0": 2500,
                            "1": 2000,
                            "2": 2000,
                            "3": 2850
                        },
                        "vsp_rpm_index": 0,
                        "priority_enabled": 0
                    },
                    "equipment": {
                        "swc_0": {
                            "vr": "V85R67",
                            "sn": "SERIALNUMBER",
                            "swc": 80,
                            "low": 0,
                            "vsp": 1,
                            "amp": 1,
                            "temp": 1,
                            "lang": 0,
                            "aux_1": {
                                "mode": 0,
                                "type": "none",
                                "color": 0,
                                "state": 0
                            },
                            "aux_2": {
                                "mode": 3,
                                "type": "heat",
                                "color": 0,
                                "state": 0
                            },
                            "sns_2": {
                                "state": 0,
                                "value": 0,
                                "sensor_type": "Orp"
                            },
                            "sns_1": {
                                "state": 1,
                                "value": 70,
                                "sensor_type": "Ph"
                            },
                            "ph_sp": 72,
                            "sns_3": {
                                "state": 1,
                                "value": 36,
                                "sensor_type": "Water temp"
                            },
                            "boost": 0,
                            "aux230": 0,
                            "orp_sp": 700,
                            "version": "V1",
                            "ph_only": 0,
                            "swc_low": 30,
                            "exo_state": 1,
                            "dual_link": 0,
                            "vsp_speed": {
                                "min": 600,
                                "max": 3450
                            },
                            "production": 0,
                            "error_code": 0,
                            "boost_time": "24:00",
                            "error_state": 0,
                            "filter_pump": {
                                "type": 2,
                                "state": 0
                            }
                        }
                    },
                    "schedules": {
                        "sch1": {
                            "id": "sch_1",
                            "name": "Salt Water Chlorinator 1",
                            "timer": {
                                "end": "12:00",
                                "start": "08:00"
                            },
                            "active": 0,
                            "enabled": 1,
                            "endpoint": "swc_1"
                        },
                        "sch2": {
                            "id": "sch_2",
                            "name": "Salt Water Chlorinator 2",
                            "timer": {
                                "end": "16:00",
                                "start": "12:00"
                            },
                            "active": 0,
                            "enabled": 1,
                            "endpoint": "swc_2"
                        },
                        "sch8": {
                            "id": "sch_8",
                            "rpm": 2850,
                            "name": "Filter Pump 4",
                            "timer": {
                                "end": "00: 00",
                                "start": "00:00"
                            },
                            "active": 0,
                            "enabled": 0,
                            "endpoint": "vsp_4"
                        },
                        "sch5": {
                            "id": "sch_5",
                            "rpm": 2500,
                            "name": "Filter Pump 1",
                            "timer": {
                                "end": "12: 00",
                                "start": "08:00"
                            },
                            "active": 0,
                            "enabled": 1,
                            "endpoint": "vsp_1"
                        },
                        "sch7": {
                            "id": "sch_7",
                            "rpm": 2000,
                            "name": "Filter Pump 3",
                            "timer": {
                                "end": "00:00",
                                "start": "00:00"
                            },
                            "active": 0,
                            "enabled": 0,
                            "endpoint": "vsp_3"
                        },
                        "sch6": {
                            "id": "sch_6",
                            "rpm": 2000,
                            "name": "Filter Pump 2",
                            "timer": {
                                "end": "16:00",
                                "start": "12:00"
                            },
                            "active": 0,
                            "enabled": 1,
                            "endpoint": "vsp_2"
                        },
                        "sch10": {
                            "id": "sch_10",
                            "name": "Aux 2",
                            "timer": {
                                "end": "00:00",
                                "start": "00:00"
                            },
                            "active": 0,
                            "enabled": 0,
                            "endpoint": "aux2"
                        },
                        "supported": 7,
                        "programmed": 4
                    },
                    "debug_main": {
                        "tr": 100
                    }
                }
            },
            "deviceId": "DEVICEID",
            "ts": 1640597135
        }

        response = MagicMock()
        response.json = async_returns(message)

        expected = {
            "vr": AqualinkSensor(system=system, data={
                "name": "vr",
                "state": "V85R67"
            }),
            "sn": AqualinkSensor(system=system, data={
                "name": "sn",
                "state": "SERIALNUMBER"
            }),
            "swc": AqualinkSensor(system=system, data={
                "name": "swc",
                "state": 80
            }),
            "low": eXOLow(system=system, data={
                "name": "low",
                "state": 0
            }),
            "vsp": AqualinkSensor(system=system, data={
                "name": "vsp",
                "state": 1
            }),
            "amp": AqualinkSensor(system=system, data={
                "name": "amp",
                "state": 1
            }),
            "temp": AqualinkSensor(system=system, data={
                "name": "temp",
                "state": 1
            }),
            "lang": AqualinkSensor(system=system, data={
                "name": "lang",
                "state": 0
            }),
            "aux_1": eXOAuxToggle(system=system, data={
                "name": "aux_1",
                "state": 0,
                "mode": 0,
                "type": "none",
                "color": 0
            }),
            "aux_2": eXOAuxToggle(system=system, data={
                "name": "aux_2",
                "state": 0,
                "mode": 3,
                "type": "heat",
                "color": 0
            }),
            "sns_2": eXOSensor(system=system, data={
                "name": "sns_2",
                "state": 0,
                "value": 0,
                "sensor_type": "Orp"
            }),
            "sns_1": eXOSensor(system=system, data={
                "name": "sns_1",
                "state": 1,
                "value": 70,
                "sensor_type": "Ph"
            }),
            "ph_sp": AqualinkSensor(system=system, data={
                "name": "ph_sp",
                "state": 72
            }),
            "sns_3": eXOSensor(system=system, data={
                "name": "sns_3",
                "state": 1,
                "value": 36,
                "sensor_type": "Water temp"
            }),
            "boost": eXOBoost(system=system, data={
                "name": "boost",
                "state": 0
            }),
            "aux230": AqualinkSensor(system=system, data={
                "name": "aux230",
                "state": 0
            }),
            "orp_sp": AqualinkSensor(system=system, data={
                "name": "orp_sp",
                "state": 700
            }),
            "version": AqualinkSensor(system=system, data={
                "name": "version",
                "state": "V1"
            }),
            "ph_only": AqualinkSensor(system=system, data={
                "name": "ph_only",
                "state": 0
            }),
            "swc_low": AqualinkSensor(system=system, data={
                "name": "swc_low",
                "state": 30
            }),
            "exo_state": AqualinkSensor(system=system, data={
                "name": "exo_state",
                "state": 1
            }),
            "dual_link": AqualinkSensor(system=system, data={
                "name": "dual_link",
                "state": 0
            }),
            "production": eXOProduction(system=system, data={
                "name": "production",
                "state": 0
            }),
            "error_code": AqualinkSensor(system=system, data={
                "name": "error_code",
                "state": 0
            }),
            "boost_time": AqualinkSensor(system=system, data={
                "name": "boost_time",
                "state": "24:00"
            }),
            "error_state": AqualinkSensor(system=system, data={
                "name": "error_state",
                "state": 0
            }),
            "filter_pump": AqualinkBinarySensor(system=system, data={
                "name": "filter_pump",
                "state": 0,
                "type": 2
            }),
            "heating": eXOThermostat(system=system, data={
                "name": "heating",
                "sp": 32,
                "state": 0,
                "sp_min": 15,
                "sp_max": 32,
                "enabled": 1,
                "vsp_rpm_list": {
                    "0": 2500,
                    "1": 2000,
                    "2": 2000,
                    "3": 2850
                },
                "vsp_rpm_index": 0,
                "priority_enabled": 0
            })
        }
        await system._parse_shadow_response(response)
        assert system.devices == expected

    async def test_parse_devices_zs500_good(self):
        data = {"id": 1, "serial_number": "ABCDEFG", "device_type": "zs500"}
        aqualink = MagicMock()
        system = AqualinkSystem.from_data(aqualink, data)

        message = {
            "state": {
                "reported": {
                    "sn": "SERIALNUMBER",
                    "dt": "zs500",
                    "vr": "8.3.0",
                    "rssi": -76,
                    "equipment": {
                        "hp_0": {
                            "cl": 1,
                            "sn": "SERIALNUMBER_HP",
                            "et": "HEAT_PUMP",
                            "md": "MD4",
                            "vr": "8.3.0",
                            "hs": "4 kW",
                            "hp": 0,
                            "st": 2,
                            "tsp": 320,
                            "fan": 643,
                            "sns_2": {
                                "type": "air",
                                "state": "connected",
                                "value": 52
                            },
                            "fanVr": 4160,
                            "sns_1": {
                                "type": "water",
                                "state": "connected",
                                "value": 257
                            },
                            "cmpVr": 100,
                            "state": 1,
                            "status": 2,
                            "reason": 6,
                            "cmprSpd": 100,
                            "errorTime": 1636021745,
                            "errorCode": "0"
                        }
                    }
                }
            },
            "deviceId": "DEVICEID",
            "ts": 1636975832
        }

        response = MagicMock()
        response.json = async_returns(message)

        expected = {
            "cl": AqualinkSensor(system=system, data={
                "name": "cl",
                "state": 1
            }),
            "sn": AqualinkSensor(system=system, data={
                "name": "sn",
                "state": "SERIALNUMBER_HP"
            }),
            "et": AqualinkSensor(system=system, data={
                "name": "et",
                "state": "HEAT_PUMP"
            }),
            "md": AqualinkSensor(system=system, data={
                "name": "md",
                "state": "MD4"
            }),
            "vr": AqualinkSensor(system=system, data={
                "name": "vr",
                "state": "8.3.0"
            }),
            "hs": AqualinkSensor(system=system, data={
                "name": "hs",
                "state": "4 kW"
            }),
            "hp": AqualinkSensor(system=system, data={
                "name": "hp",
                "state": 0
            }),
            "st": AqualinkSensor(system=system, data={
                "name": "st",
                "state": 2
            }),
            "tsp": AqualinkSensor(system=system, data={
                "name": "tsp",
                "state": 320
            }),
            "fan": AqualinkSensor(system=system, data={
                "name": "fan",
                "state": 643
            }),
            "sns_2": eXOSensor(system=system, data={
                "name": "sns_2",
                "state": "connected",
                "type": "air",
                "value": 52
            }),
            "fanVr": AqualinkSensor(system=system, data={
                "name": "fanVr",
                "state": 4160
            }),
            "sns_1": eXOSensor(system=system, data={
                "name": "sns_1",
                "state": "connected",
                "type": "water",
                "value": 257
            }),
            "cmpVr": AqualinkSensor(system=system, data={
                "name": "cmpVr",
                "state": 100
            }),
            "state": AqualinkSensor(system=system, data={
                "name": "state",
                "state": 1
            }),
            "status": AqualinkSensor(system=system, data={
                "name": "status",
                "state": 2
            }),
            "reason": AqualinkSensor(system=system, data={
                "name": "reason",
                "state": 6
            }),
            "cmprSpd": AqualinkSensor(system=system, data={
                "name": "cmprSpd",
                "state": 100
            }),
            "errorTime": AqualinkSensor(system=system, data={
                "name": "errorTime",
                "state": 1636021745
            }),
            "errorCode": AqualinkSensor(system=system, data={
                "name": "errorCode",
                "state": "0"
            })
        }
        await system._parse_shadow_response(response)
        assert system.devices == expected
