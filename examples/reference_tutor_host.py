"""Source-checkout wrapper for the installed anatomy demo.

The composition uses TutorHostRunner, ScriptedTutorDecisionPort, and
OpenAIResponsesTutorDecisionPort in ``cardine.demo.anatomy``.
"""

from cardine.demo.anatomy import (
    COURSE_ID,
    SESSION_ID,
    _capture_descriptor,
    _DemoAssembler,
    _DemoAuthority,
    _DemoGateway,
    _DemoIdentity,
    _DemoStore,
    _FixedClock,
    _inputs,
    _MemorySource,
    main,
    run_reference_demo,
)

__all__ = [
    "COURSE_ID",
    "SESSION_ID",
    "_DemoAssembler",
    "_DemoAuthority",
    "_DemoGateway",
    "_DemoIdentity",
    "_DemoStore",
    "_FixedClock",
    "_MemorySource",
    "_capture_descriptor",
    "_inputs",
    "main",
    "run_reference_demo",
]


if __name__ == "__main__":
    main()
