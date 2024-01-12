import asyncio
import json
import logging
import time
from math import floor
from tkinter import messagebox

import aiofiles
import anyio
import configargparse
from async_timeout import timeout

import gui

CONNECTION_TIMEOUT = 1
RECONNECT_TIMEOUT = 1
PING_MSG = ''

watchdog_logger = logging.getLogger('watchdog')
logging.basicConfig(level=logging.DEBUG)

messages_queue = asyncio.Queue()
sending_queue = asyncio.Queue()
status_updates_queue = asyncio.Queue()
saving_queue = asyncio.Queue()
watchdog_queue = asyncio.Queue()


class InvalidToken(Exception):
    pass


async def generate_msgs(queue):
    while True:
        queue.put_nowait(PING_MSG)
        await asyncio.sleep(0.9)


async def read_msgs(host, port, queue):
    try:
        status_updates_queue.put_nowait(gui.ReadConnectionStateChanged.INITIATED)
        reader, writer = await asyncio.open_connection(host, port)
        status_updates_queue.put_nowait(gui.ReadConnectionStateChanged.ESTABLISHED)

        while True:
            chat_line = await reader.readline()
            chat_line = chat_line.decode()
            queue.put_nowait(chat_line.rstrip())
            saving_queue.put_nowait(chat_line)
            watchdog_queue.put_nowait("New message in chat")
    except (asyncio.exceptions.CancelledError, Exception) as e:
        if 'writer' in locals():
            writer.close()
        status_updates_queue.put_nowait(gui.ReadConnectionStateChanged.CLOSED)
        raise


async def authorise(reader, writer):
    _greeting_prompt = await reader.readline()
    watchdog_queue.put_nowait("Prompt before auth")
    await submit_message(args.token, writer)

    json_response = await reader.readline()
    json_response = json.loads(json_response.decode())

    if json_response is None:
        raise InvalidToken

    nickname = json_response['nickname']
    status_updates_queue.put_nowait(gui.NicknameReceived(nickname))
    watchdog_queue.put_nowait("Authorization done")
    print(f"Выполнена авторизация. Пользователь {nickname}.")


async def submit_message(text, writer):
    writer.write(f'{text}\n'.encode())
    await writer.drain()


async def send_msgs(host, port, queue):
    try:
        status_updates_queue.put_nowait(gui.SendingConnectionStateChanged.INITIATED)
        reader, writer = await asyncio.open_connection(host, port)
        status_updates_queue.put_nowait(gui.SendingConnectionStateChanged.ESTABLISHED)
        await authorise(reader, writer)

        while True:
            message = await queue.get()
            if message == PING_MSG:
                await ping_pong(reader, writer)
            else:
                await submit_message(message + '\n', writer)
            watchdog_queue.put_nowait("Message sent")
    except (asyncio.exceptions.CancelledError, Exception) as e:
        if 'writer' in locals():
            writer.close()
        status_updates_queue.put_nowait(gui.SendingConnectionStateChanged.CLOSED)
        raise


async def ping_pong(reader, writer):
    await submit_message(PING_MSG, writer)
    try:
        async with timeout(CONNECTION_TIMEOUT):
            _pong = await reader.readline()
    except TimeoutError:
        logging.debug("Ping failed")
        raise ConnectionError


async def save_messages(filepath, queue):
    async with aiofiles.open(filepath, "a", buffering=1) as history_file:
        while True:
            chat_line = await queue.get()
            await history_file.write(chat_line)


async def watch_for_connection():
    while True:
        try:
            async with timeout(CONNECTION_TIMEOUT):
                msg = await watchdog_queue.get()
                watchdog_logger.debug(f"[{floor(time.time())}] Connection is alive. {msg}")
        except TimeoutError:
            watchdog_logger.debug(f"[{floor(time.time())}] {CONNECTION_TIMEOUT}s timeout is elapsed")
            raise ConnectionError


async def handle_connection():
    while True:
        try:
            async with anyio.create_task_group() as tg:
                tg.start_soon(read_msgs, args.listen_host, args.listen_port, messages_queue)
                tg.start_soon(send_msgs, args.send_host, args.send_port, sending_queue)
                tg.start_soon(watch_for_connection)
        except ExceptionGroup as e:
            logging.debug(f'handle_connection(): {type(e)} {e} {e.exceptions}')
            tg.cancel_scope.cancel()
            await asyncio.sleep(RECONNECT_TIMEOUT)


async def main():
    load_history(args.filepath, messages_queue)
    try:
        async with anyio.create_task_group() as tg:
            tg.start_soon(gui.draw, messages_queue, sending_queue, status_updates_queue),
            tg.start_soon(generate_msgs, sending_queue),
            tg.start_soon(save_messages, args.filepath, saving_queue),
            tg.start_soon(handle_connection)
    except* gui.TkAppClosed:
        tg.cancel_scope.cancel()


def load_history(filepath, queue):
    try:
        with open(filepath, 'r') as history_file:
            chat_line = history_file.readline().rstrip()
            while chat_line:
                queue.put_nowait(chat_line)
                chat_line = history_file.readline().rstrip()
    except FileNotFoundError:
        print('Файл истории не найден, создаю новый')


def prepare_args():
    parser = configargparse.ArgParser(default_config_files=['config.ini'])
    parser.add_argument('--listen_host', type=str,
                        help='listen host')
    parser.add_argument('--listen_port', type=int,
                        help='listen port')
    parser.add_argument('--send_host', type=str,
                        help='send host')
    parser.add_argument('--send_port', type=int,
                        help='send port')
    parser.add_argument('-t', '--token', type=str,
                        help='token of existing user')
    parser.add_argument('-f', '--filepath', type=str, default='./history.txt',
                        help='file path to save chat history')
    return parser.parse_args()


if __name__ == '__main__':
    args = prepare_args()
    try:
        asyncio.run(main())
    except InvalidToken:
        messagebox.showinfo("Неверный токен", "Проверьте токен, сервер его не узнал")
    except KeyboardInterrupt:
        quit()

