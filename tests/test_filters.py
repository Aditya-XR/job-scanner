import pytest

from jobscan.filters import experience_check, is_india, is_software


@pytest.mark.parametrize("desc,decision", [
    ("We need 0-2 years of experience in Java", "keep"),
    ("0-1 years of experience in Java", "keep"),
    ("Build great things with Python. Founded 15 years ago.", "keep"),     # no requirement stated
    ("Minimum 3+ years of professional experience building APIs", "drop"),
    ("You have two or more years of industry experience", "drop"),
    ("1-3 yrs exp in Node", "drop"),
    ("BE/B.Tech 8+ plus years professional experience", "drop"),
    ("Bachelors + 2 years of related experience OR Masters + 0 years of related experience.", "drop"),
    ("MS + 0-2 years or BS + 1-3 years of experience", "drop"),
    ("Freshers welcome! 2+ years of experience preferred", "unclear"),
])
def test_experience(desc, decision):
    assert experience_check("Software Engineer", desc)[0] == decision


def test_entry_titles_skip_the_experience_check():
    assert experience_check("SDE Intern", "3 years experience")[0] == "keep"


def test_experience_in_title_counts():
    assert experience_check("Engineer | 8+ years exp", "")[0] == "drop"


@pytest.mark.parametrize("title,ok", [
    ("Software Engineer", True), ("Associate Software Engineer", True), ("Software Engineer I", True),
    ("Frontend Developer", True), ("MTS", True),
    ("Software Engineer II", False), ("Senior Software Engineer", False), ("SDE-2", False),
    ("Lead MTS", False), ("Sales Engineer", False), ("Hardware Engineer", False),
    ("Software Engineering Technical Leader", False), ("Product Security Engineer 5", False),
])
def test_is_software(title, ok):
    assert is_software(title) is ok


@pytest.mark.parametrize("loc,ok", [
    ("Bengaluru, Karnataka", True), ("Hyderabad, Telangana, IND", True), ("Remote - APAC", True),
    ("India, Telangana, Hyderabad", True),
    ("Indianapolis, IN", False), ("Indiana", False), ("Remote, US", False),
])
def test_is_india(loc, ok):
    assert is_india(loc) is ok


@pytest.mark.parametrize("desc,expected", [
    ("Build great things with Python. Founded 15 years ago.", ("keep", "Not mentioned")),
    ("Join us. For over 20 years we have built software. You need 3+ years of experience.", ("drop", "3+ yrs")),
    ("Our company was founded 12 years ago. 0-1 years of experience required.", ("keep", "0-1 yrs")),
    # real NVIDIA text: company history on the line before a real requirement
    ("accelerated computing for more than 25 years. It's a unique legacy of innovation.\n"
     "8+ years of SAP experience with Core focus on SAP Production Planning", ("drop", "8+ yrs")),
    # real ServiceNow / Citi text that slipped through before
    ("2 to 5 years of Python engineering expertise with a focus on building production-grade code", ("drop", "2-5 yrs")),
    ("5-8 years in an Apps Development role. Demonstrated execution capabilities.", ("drop", "5-8 yrs")),
])
def test_requirements_vs_company_history(desc, expected):
    assert experience_check("Software Engineer", desc)[:2] == expected


def test_title_words_are_not_context_for_the_description():
    assert experience_check("Software Engineer", "Founded 15 years ago. Python.")[0] == "keep"


@pytest.mark.parametrize("title,ok", [
    ("Full Time - Back End Clerk - Day", False), ("Back End Developer", True), ("Front-end Engineer", True),
    ("Dir, Software Engrg Mgmt", False), ("Software Engineering Mgr", False),
    ("Software Engineer, Identity & Access Management", True), ("Data Management Platform Engineer", True),
])
def test_back_end_needs_an_engineering_word(title, ok):
    assert is_software(title) is ok
