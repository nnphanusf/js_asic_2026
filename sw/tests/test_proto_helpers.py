"""Host-side protocol helpers in bitweaver.proto: frame builders, decoders and the SWD sequencer."""

import pytest

from bitweaver.proto import (SwdSeq, can_crc15, can_frame_bits, can_rx_decode, can_rx_frame, swd_run,
                             swd_transfer, usb_rx_decode)


# ---------------------------------------------------------------- decoders never crash on short input
@pytest.mark.parametrize("decode, words", [
    (usb_rx_decode, [0x6980, 0, 16]),         # an IN token with no address field
    (usb_rx_decode, [0xD280, 0x9900, 24]),    # ACK followed by a stray byte
    (can_rx_decode, [0, 0, 19]),              # a header announcing data that is not there
])
def test_decoders_reject_short_or_long_packets(decode, words):
    assert decode(words)[2] is False


def _can_rx_words(bits):
    """Pack frame bits the way can_rx pushes them: 16-bit words MSB first, the rest right-aligned,
    then the bit count."""
    full, rest = divmod(len(bits), 16)
    words = [int("".join(map(str, bits[16 * i:16 * i + 16])), 2) for i in range(full)]
    tail = bits[16 * full:]
    words.append(int("".join(map(str, tail)), 2) if tail else 0)
    return words + [len(bits)]


def _with_crc(bits):
    crc = can_crc15(bits)
    return bits + [(crc >> i) & 1 for i in range(14, -1, -1)]


def test_can_decode_needs_the_data_and_crc_the_dlc_announces():
    bits = _with_crc(can_frame_bits(0x55, b"\x01\x02"))
    assert can_rx_decode(_can_rx_words(bits)) == (0x55, b"\x01\x02", True)
    for cut in (20, 30, len(bits) - 1):
        assert can_rx_decode(_can_rx_words(bits[:cut]))[2] is False


# ---------------------------------------------------------------- CAN remote frames
def test_can_remote_frame_has_no_data_field():
    bits = can_frame_bits(0x123, rtr=True, dlc=1)
    assert len(bits) == 19 and bits[12] == 1 and bits[15:19] == [0, 0, 0, 1]
    assert can_rx_decode(_can_rx_words(_with_crc(bits))) == (0x123, b"", True)
    # can_rx_frame tells a remote request from an empty data frame, and gives the requested length.
    for dlc in (0, 1, 4, 8):
        words = _can_rx_words(_with_crc(can_frame_bits(0x123, rtr=True, dlc=dlc)))
        assert can_rx_frame(words) == (0x123, True, dlc, b"", True)
    assert can_rx_frame(_can_rx_words(_with_crc(can_frame_bits(0x123)))) == (0x123, False, 0, b"", True)
    data = can_frame_bits(0x123, b"\xA5")
    assert len(data) == 27
    assert can_rx_decode(_can_rx_words(_with_crc(data))) == (0x123, b"\xA5", True)
    with pytest.raises(ValueError):
        can_frame_bits(0x123, b"\xA5", rtr=True)
    with pytest.raises(ValueError):
        can_frame_bits(0x123, b"\xA5", dlc=2)


# ---------------------------------------------------------------- SWD
def test_jtag_to_swd_has_no_gap_before_the_select_word():
    seq = SwdSeq()
    seq.jtag_to_swd()
    writes = [(seq.words[i] & 0x0F) + 1 for i in range(0, 10, 2)], seq.words[1:10:2]
    assert writes == ([16] * 5, [0xFFFF] * 4 + [0xE79E])


def _fake_target(acks):
    """A run(words, n) for swd_run: answers ACKs from `acks` and records every phase's words."""
    phases = []

    def run(words, n):
        phases.append(words)
        if len(phases) % 2:  # the ACK phase: turnaround, ACK
            return [0, acks.pop(0) << 13]
        return [0] * n

    return run, phases


def test_swd_write_sends_no_data_after_wait():
    run, phases = _fake_target([SwdSeq.WAIT, SwdSeq.WAIT, SwdSeq.OK])
    assert swd_run(swd_transfer(1, 0, 0x4, 0xDEADBEEF), run) == (SwdSeq.OK, None, True)
    data_phases = phases[1::2]
    assert [0xBEEF in p for p in data_phases] == [False, False, True]


def test_swd_gives_up_after_fault_and_retries():
    run, phases = _fake_target([SwdSeq.FAULT])
    assert swd_run(swd_transfer(0, 1, 0x0), run) == (SwdSeq.FAULT, None, False)
    assert len(phases) == 2
    run, phases = _fake_target([SwdSeq.WAIT] * 3)
    assert swd_run(swd_transfer(0, 1, 0x0, retries=2), run) == (SwdSeq.WAIT, None, False)
