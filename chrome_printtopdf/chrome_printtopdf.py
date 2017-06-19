import asyncio
import base64
import json
import logging
from io import BytesIO
import tempfile
import time
import subprocess

import aiohttp

logger = logging.getLogger(__name__)

DEFAULT_HOST = '127.0.0.1'
DEFAULT_PORT = 9222


def get_loop():
    try:
        return asyncio.get_event_loop()
    except RuntimeError:
        pass
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop


async def get_debug_url(session, host=DEFAULT_HOST, port=DEFAULT_PORT):
    debug_url = 'http://%s:%s/json/list' % (host, port)
    logger.debug('Getting debug URL...')
    async with session.get(debug_url) as resp:
        assert resp.status == 200
        resp = json.loads(await resp.text())
        return resp[0]['webSocketDebuggerUrl']


def send_message(ws, command):
    logger.debug('-> %s', command[1])
    ws.send_str(json.dumps(command[1]))
    return command[0], command[1]['id'], command[2]


async def send_print_command(ws, print_url):
    command_list = [
        (None, {"id": 1, "method": "Page.enable", "params": {}}, 0),
        ('Page.frameStoppedLoading', {"id": 2, "method": "Page.navigate",
            "params": {
                "url": print_url
            }
        }, 1),
        (None, {'id': 3, "method": "Page.stopLoading"}, 1),
        (None, {"id": 4, "method": "Page.printToPDF", "params": {}}, 0),
    ]
    pdf_bytes = None
    wait_for_event = send_message(ws, command_list[0])
    index = 1
    async for msg in ws:
        if msg.type == aiohttp.WSMsgType.TEXT:
            result = json.loads(msg.data)
            if 'error' in result:
                raise Exception(str(result))
            logger.debug('<- %s', result)
            if ((wait_for_event[0] is None and
                        result.get('id') == wait_for_event[1]) or
                        result.get('method') == wait_for_event[0]):

                await asyncio.sleep(wait_for_event[2])

                if index < len(command_list):
                    wait_for_event = send_message(ws, command_list[index])
                    index += 1
                else:
                    pdf_bytes = base64.b64decode(result['result']['data'])
                    await ws.close()
                    break
        elif msg.type == aiohttp.WSMsgType.CLOSED:
            break
        elif msg.type == aiohttp.WSMsgType.ERROR:
            break
    return pdf_bytes


async def wait_for_port(ip, port, num_tries=3, timeout=5, loop=None):
    fut = asyncio.open_connection(ip, port, loop=loop)
    writer = None
    try:
        for tries in range(num_tries):
            try:
                _, writer = await asyncio.wait_for(fut, timeout=timeout)
            except asyncio.TimeoutError:
                logger.debug('Timeout %d connecting to chrome...', tries + 1)
            except Exception as exc:
                logger.debug('Error {}:{} {}'.format(ip, port, exc))
            logger.debug("{}:{} Connected".format(ip, port))
            break
    finally:
        if writer is not None:
            writer.close()


async def get_pdf(url, loop=None, host=DEFAULT_HOST, port=DEFAULT_PORT):
    if loop is None:
        loop = get_loop()
    async with aiohttp.ClientSession(loop=loop) as session:
        debugger_url = await get_debug_url(session, host=host, port=port)
        logger.debug('Connecting to %s', debugger_url)
        async with session.ws_connect(debugger_url) as ws:
            pdf_bytes = await send_print_command(ws, url)
            if pdf_bytes is None:
                raise Exception('Could not get PDF.')
            return BytesIO(pdf_bytes)


class BaseChromeContextManager:
    def __init__(self, chrome_binary='/usr/local/bin/chromium',
                             host=DEFAULT_HOST, port=9222):
        self.chrome_binary = chrome_binary
        self.host = host
        self.port = port

    def prepare_call(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.args = [self.chrome_binary,
            '--headless',
            '--disable-gpu',
            '--remote-debugging-port=%s' % self.port,
            '--remote-debugging-address=%s' % self.host,
            '--user-data-dir=%s' % self.temp_dir.name]
        logger.debug('Starting chrome: %s', self.chrome_binary)


class AsyncChromeContextManager(BaseChromeContextManager):
    def __init__(self, loop=None, **kwargs):
        if loop is None:
            loop = get_loop()
        self.loop = loop
        super().__init__(**kwargs)

    async def __aenter__(self):
        asyncio.get_child_watcher().attach_loop(self.loop)
        self.proc = await asyncio.create_subprocess_exec(*self.args,
                                                         loop=self.loop)
        logger.debug('Started, waiting for debug port...')
        await asyncio.sleep(1)  # Not sure why this is necessary...
        await wait_for_port(self.host, self.port, loop=self.loop)
        logger.debug('Debug port available.')

    async def __aexit__(self, exc_type, exc, tb):
        logger.debug('Terminating chrome...')
        self.proc.terminate()
        await self.proc.wait()
        self.temp_dir.cleanup()
        logger.debug('Chrome terminated.')


class ChromeContextManager(BaseChromeContextManager):
    def __enter__(self):
        self.prepare_call()

        self.proc = subprocess.Popen(
                self.args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
        )
        time.sleep(1)
        logger.debug('Chrome started.')

    def __exit__(self, exc_type, exc, tb):
        logger.debug('Terminating chrome...')
        self.proc.terminate()
        self.proc.wait()
        self.temp_dir.cleanup()
        logger.debug('Chrome terminated.')


async def get_pdf_with_chrome(url, loop=None, **chrome_options):
    if loop is None:
        loop = get_loop()
    with ChromeContextManager(**chrome_options):
        return await get_pdf(url, loop,
                             host=chrome_options.get('host', DEFAULT_HOST),
                             port=chrome_options.get('port', DEFAULT_PORT),
                     )


def get_pdf_sync(url, **options):
    loop = get_loop()
    return loop.run_until_complete(get_pdf(url, loop=loop, **options))


def get_pdf_with_chrome_sync(url, **chrome_options):
    loop = get_loop()
    return loop.run_until_complete(get_pdf_with_chrome(url, loop=loop,
                                                       **chrome_options))
