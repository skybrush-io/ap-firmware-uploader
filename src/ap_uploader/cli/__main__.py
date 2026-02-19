import logging
from argparse import ArgumentParser
from contextlib import AsyncExitStack, aclosing

from ap_uploader.scanners.fixed import FixedTargetList
from ap_uploader.scanners.serial import SerialPortScanner
from ap_uploader.scanners.udp import UDPMAVLinkHeartbeatScanner
from ap_uploader.uploader import LogEvent, Uploader, UploaderEventHandler

from .rich_ui import RichConsoleUI


def create_parser() -> ArgumentParser:
    """Creates the command line parser for the uploader CLI."""
    parser = ArgumentParser(description="ArduPilot/PX4 firmware uploader")
    parser.add_argument(
        "firmware",
        help="path to the firmware file to upload",
        default=None,
        nargs="?",
    )
    parser.add_argument(
        "--max-concurrency",
        metavar="NUM_TASKS",
        type=int,
        help="allow at most NUM_TASKS parallel upload tasks to be running at the same time",
        default=0,
    )
    parser.add_argument(
        "--retries",
        metavar="COUNT",
        type=int,
        help="retry failed upload tasks COUNT times before giving up",
        default=3,
    )
    parser.add_argument(
        "-p",
        "--port",
        help="serial port or IP address that the uploader will send data to. "
        'Use "udp" to listen for incoming MAVLink heartbeats on UDP port 14550. '
        "Omit to scan the serial ports for devices sending MAVLink heartbeats.",
        action="append",
    )
    parser.add_argument(
        "--factory-reset",
        help="perform a factory reset instead of uploading a firmware",
        action="store_true",
    )
    return parser


async def uploader(options, on_event: UploaderEventHandler) -> None:
    ports: list[str] = options.port
    max_concurrency: int = max(options.max_concurrency, 0)
    retries: int = max(options.retries, 0)

    if options.firmware is not None:
        if options.factory_reset:
            raise RuntimeError(
                "cannot use --factory-reset together with a firmware image"
            )

    async with AsyncExitStack() as stack:
        up = await stack.enter_async_context(Uploader().use())
        if options.firmware is not None:
            await up.load_firmware(options.firmware)

        upload_task_group = await stack.enter_async_context(
            up.create_task_group(
                on_event=on_event,
                max_concurrency=max_concurrency,
                retries=retries,
            )
        )

        if ports:
            if len(ports) == 1 and ports[0] == "udp":
                on_event(
                    "",
                    LogEvent(
                        logging.INFO,
                        "Listening on UDP port 14550 for MAVLink heartbeats, ^C to exit...",
                    ),
                )
                socket = await up.get_shared_udp_socket()
                scanner = UDPMAVLinkHeartbeatScanner(socket)
            else:
                scanner = FixedTargetList(ports)
        else:
            on_event(
                "",
                LogEvent(
                    logging.INFO,
                    "Finding serial ports transmitting MAVLink heartbeats, ^C to exit...",
                ),
            )
            scanner = SerialPortScanner()

        upload_target_generator = up.generate_targets_from(scanner)
        async with aclosing(upload_target_generator) as upload_targets:
            if options.firmware is not None:
                async for target in upload_targets:
                    upload_task_group.start_upload_to(target)

            if options.factory_reset:
                async for target in upload_targets:
                    upload_task_group.start_factory_reset_on(target)


def main() -> None:
    from anyio import run

    parser = create_parser()
    options = parser.parse_args()

    try:
        with RichConsoleUI() as ui:
            run(uploader, options, ui.handle_event)
    except KeyboardInterrupt:
        pass
