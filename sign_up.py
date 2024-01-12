import asyncio
import json
import tkinter as tk

import anyio
import configargparse
from async_timeout import timeout

CONNECTION_TIMEOUT = 7

sending_queue = asyncio.Queue()
incoming_queue = asyncio.Queue()
disabled_input = True


def process_new_message(input_field):
    if disabled_input:
        return

    text = input_field.get().strip()
    if text == '':
        return

    sending_queue.put_nowait(text)
    input_field.delete(0, tk.END)


async def connect_to_server(host, port):
    try:
        reader, writer = await asyncio.open_connection(host, port)
        async with timeout(CONNECTION_TIMEOUT):
            greeting_prompt = await reader.readline()
            await submit_message('', writer)
            new_user_prompt = await reader.readline()

        new_user_prompt = new_user_prompt.decode().rstrip()
        incoming_queue.put_nowait(new_user_prompt)

        global disabled_input
        disabled_input = False
        nickname = await sending_queue.get()
        incoming_queue.put_nowait("Ожидаем подтверждения сервера...")

        async with timeout(CONNECTION_TIMEOUT):
            await submit_message(nickname, writer)
            json_response = await reader.readline()

        account_hash = json.loads(json_response.decode())["account_hash"]
        with open('config.ini', 'a') as f:
            f.write(f'\ntoken={account_hash}\n')
        incoming_queue.put_nowait("Регистрация прошла успешно! Можете закрыть окно")
    except Exception as e:
        if 'writer' in locals():
            writer.close()
        incoming_queue.put_nowait("Не удалось подключиться к серверу, проверьте соединение с Интернетом")


async def submit_message(text, writer):
    writer.write(f'{text}\n'.encode())
    await writer.drain()


async def update_tk(root_frame, interval=1 / 120):
    while True:
        root_frame.update()
        await asyncio.sleep(interval)


async def update_hints(panel):
    while True:
        msg = await incoming_queue.get()
        panel['text'] = msg


async def draw():
    root = tk.Tk()

    root.title('Регистрация Майнкрафтера')

    root_frame = tk.Frame()
    root_frame.pack(fill="both", expand=True)

    hint_frame = tk.Frame(root_frame)
    hint_frame.pack(side="top", fill=tk.X)
    hint_label = tk.Label(hint_frame, height=2, width=90, fg='grey', font='arial 16', anchor='w')
    hint_label.pack(side="top", fill=tk.X)
    hint_label['text'] = "Подождите, происходит подключение к серверу..."

    input_frame = tk.Frame(root_frame)
    input_frame.pack(side="bottom", fill=tk.X)

    input_field = tk.Entry(input_frame)
    input_field.pack(side="left", fill=tk.X, expand=True)
    input_field.bind("<Return>", lambda event: process_new_message(input_field))

    send_button = tk.Button(input_frame)
    send_button["text"] = "ОК"
    send_button["command"] = lambda: process_new_message(input_field)
    send_button.pack(side="left")

    def on_closing():
        root.destroy()
        tg.cancel_scope.cancel()

    root.protocol("WM_DELETE_WINDOW", on_closing)

    async with anyio.create_task_group() as tg:
        tg.start_soon(update_tk, root_frame),
        tg.start_soon(update_hints, hint_label),
        tg.start_soon(connect_to_server, args.send_host, args.send_port)


def prepare_args():
    parser = configargparse.ArgParser(default_config_files=['config.ini'], ignore_unknown_config_file_keys=True)
    parser.add_argument('--send_host', type=str,
                        help='send host')
    parser.add_argument('--send_port', type=int,
                        help='send port')
    return parser.parse_args()


if __name__ == '__main__':
    args = prepare_args()
    try:
        asyncio.run(draw())
    except KeyboardInterrupt:
        quit()
