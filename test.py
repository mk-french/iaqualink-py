import asyncio
import logging
import keyring
import time
import threading

from src.iaqualink.client import AqualinkClient


USERNAME = 'martin.frenchy@gmail.com'
#keyring.set_password("exotest", USERNAME, "<passwordhere>")
PASSWORD = keyring.get_password("exotest", USERNAME)

logging.basicConfig() # set basic config so debugs are printed
LOGGER = logging.getLogger("iaqualink")
LOGGER.setLevel(logging.DEBUG)

async def print_devices():
    async with AqualinkClient(USERNAME, PASSWORD) as c:
        s = await c.get_systems()
        print("\nGot Systems...\n")
        shadow=list(s.values())[0].shadow
        print(shadow.reported)
        
        d = await list(s.values())[0].get_devices()
        print("\nGot Devices...\n")
        print(d)
        
        time.sleep(1000)

        """ # test change value
        desired_state = {'heating': {'enabled': 1}}
        shadow.change_shadow_state(desired_state)
        time.sleep(15)
        print(shadow.reported)

        # test change value
        desired_state = {'heating': {'enabled': 0}}
        shadow.change_shadow_state(desired_state)
        time.sleep(15)
        print(shadow.reported)"""

def main():
    print(threading.get_ident())
    loop = asyncio.get_event_loop()
    loop.run_until_complete(print_devices())
    loop.close()
    

if __name__ == '__main__':
    main()