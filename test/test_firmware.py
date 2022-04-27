from io import BytesIO
from pytest import raises

from ap_uploader.firmware import APJFirmware, load_firmware


HELLO_FIRMWARE = b'{ "image": "eJzLSM3JyQcABiwCFQ==", "board_id": 9 }'
HELLO_FIRMWARE_WITH_SIZE = (
    b'{ "image": "eJzLSM3JyQcABiwCFQ==", "board_id": 9, "image_size": 42 }'
)


def test_apj_firmware():
    fw = APJFirmware()
    fw.read_sync(BytesIO(HELLO_FIRMWARE))

    assert fw.image == b"hello"
    assert fw.image_size == 5
    assert fw.crc == 0xF032519B
    assert fw.metadata == {"board_id": 9}
    assert fw.board_id == 9
    assert fw.board_revision is None

    # ask for CRC once again to test the code path that returns the cached CRC
    assert fw.crc == 0xF032519B


def test_apj_firmware_without_load():
    fw = APJFirmware()
    with raises(RuntimeError, match="image has not been loaded yet"):
        fw.image


async def test_load_firmware(tmp_path):
    tmp_file = tmp_path / "test.apj"
    with tmp_file.open("wb") as fp:
        fp.write(HELLO_FIRMWARE_WITH_SIZE)

    fw = await load_firmware(tmp_file)

    assert fw.image == b"hello"
    assert fw.image_size == 42  # fake size
    assert fw.crc == 0xF032519B
    assert fw.metadata == {"board_id": 9, "image_size": 42}
    assert fw.board_id == 9
    assert fw.board_revision is None
