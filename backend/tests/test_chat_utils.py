"""
Tests for chat_utils.split_question_parts (Fix round 2, item 7).
"""
from chat_utils import split_question_parts


def test_ordinary_question_is_not_split():
    question = "What do bees do for plants and flowers in a typical garden?"
    assert split_question_parts(question) == [question]


def test_plain_x_and_y_question_is_not_split():
    question = "What is a firewall and how does it work?"
    assert split_question_parts(question) == [question]


def test_numbered_list_on_one_line_is_split_into_its_items():
    question = (
        "1) Explain firewalls and IDS placement. 2) Explain encryption "
        "basics. 3) Explain subnetting with CIDR."
    )
    parts = split_question_parts(question)
    assert parts == [
        "Explain firewalls and IDS placement.",
        "Explain encryption basics.",
        "Explain subnetting with CIDR.",
    ]


def test_lettered_list_is_split_into_its_items():
    question = "a) What is NAT? b) What is DHCP? c) What is a VLAN?"
    parts = split_question_parts(question)
    assert parts == ["What is NAT?", "What is DHCP?", "What is a VLAN?"]


def test_multi_line_pasted_brief_is_split_one_part_per_line():
    question = (
        "Explain firewalls and IDS placement.\n"
        "Explain the TLS handshake.\n"
        "Explain subnetting with CIDR."
    )
    parts = split_question_parts(question)
    assert parts == [
        "Explain firewalls and IDS placement.",
        "Explain the TLS handshake.",
        "Explain subnetting with CIDR.",
    ]


def test_semicolon_separated_list_is_split():
    question = "Explain NAT; explain DHCP; explain VLANs"
    parts = split_question_parts(question)
    assert parts == ["Explain NAT", "explain DHCP", "explain VLANs"]


def test_long_comma_separated_list_is_split():
    question = (
        "Please explain in detail firewalls and IDS placement, encryption "
        "basics, the TLS handshake steps, subnetting with CIDR, and Linux "
        "hardening basics for my assignment."
    )
    parts = split_question_parts(question)
    assert parts == [
        "Please explain in detail firewalls and IDS placement",
        "encryption basics",
        "the TLS handshake steps",
        "subnetting with CIDR",
        "Linux hardening basics for my assignment.",
    ]


def test_short_comma_list_is_not_split():
    # Only two items and well under the word-count floor -- this should
    # read as one ordinary question, not a multi-part one.
    question = "Explain photosynthesis, briefly"
    assert split_question_parts(question) == [question]


def test_empty_question_is_returned_unchanged():
    assert split_question_parts("") == [""]


def test_number_of_parts_is_capped():
    question = "\n".join(f"Explain topic {i}." for i in range(1, 10))
    parts = split_question_parts(question)
    assert len(parts) == 5
