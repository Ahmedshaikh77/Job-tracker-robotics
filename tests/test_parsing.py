from src.parsing import JobSections, split_job_sections


def test_split_job_sections_keeps_required_responsibility_and_preferred_context():
    sections = split_job_sections(
        """
        Responsibilities
        Build fixtures with a multidisciplinary team.
        Minimum Qualifications
        2 years of Python experience.
        Preferred Qualifications
        5 years of robotics experience.
        """
    )
    assert isinstance(sections, JobSections)
    assert sections.responsibilities == (
        "Build fixtures with a multidisciplinary team.",
    )
    assert sections.required == ("2 years of Python experience.",)
    assert sections.preferred == ("5 years of robotics experience.",)


def test_inline_heading_content_is_not_lost():
    sections = split_job_sections(
        "Requirements: Python and C++. Nice to have: ROS 2."
    )
    assert sections.required == ("Python and C++.",)
    assert sections.preferred == ("ROS 2.",)


def test_unheaded_text_is_other_and_full_text_is_normalized():
    sections = split_job_sections("  Build   robotic systems.\nTest hardware.  ")
    assert sections.other == ("Build robotic systems.", "Test hardware.")
    assert sections.full_text == "Build robotic systems. Test hardware."
