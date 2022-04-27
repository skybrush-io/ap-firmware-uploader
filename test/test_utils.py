from ap_uploader.utils import crc32


def test_crc32():
    assert crc32(b"") == 0
    assert crc32(b"spam ham") == 0xC5AE5BFE
    assert crc32(b"spam ham bacon") == 0x3FB1431D
    assert crc32(b" bacon", state=0xC5AE5BFE) == 0x3FB1431D
