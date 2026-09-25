# =============================================================================
# PH Agent Hub — Web Research Report Workflow
# =============================================================================
# A two-step workflow for comprehensive web research and report generation:
#   Step 1 (research):  Search the web for relevant information on a topic
#   Step 2 (report):    Synthesize findings into a structured report
#
# This module is auto-registered by the MAF registry (exposes MAF_KEY).
# =============================================================================

MAF_KEY = "web_research_report"
NAME = "Web Research Report"
DESCRIPTION = (
    "Multi-step workflow that searches the web for information on a topic "
    "and synthesizes the findings into a structured research report."
)

STEPS = [
    {
        # Step 1: Research step — search and collect information
        "id": "research",
        "name": "Web Research",
        "type": "inline",
        "instructions": (
            "Research the following topic thoroughly. Use web search to find "
            "relevant, up-to-date information. Look for multiple sources and "
            "key details. Return a comprehensive summary of your findings "
            "including:\n"
            "- Key facts and figures\n"
            "- Notable sources and context\n"
            "- Current developments or trends\n"
            "- Any relevant background information\n\n"
            "Be thorough and cite the types of sources you found."
        ),
        "model_ref": "@reasoning",
    },
    {
        # Step 2: Report step — synthesize into structured report
        "id": "report",
        "name": "Report Writer",
        "type": "inline",
        "instructions": (
            "You are a professional research analyst. Synthesize the research "
            "findings provided below into a well-structured, readable report.\n\n"
            "The report should include:\n"
            "1. **Executive Summary** — Brief overview of the topic\n"
            "2. **Key Findings** — Main points organized logically\n"
            "3. **Detailed Analysis** — Deeper exploration of important aspects\n"
            "4. **Conclusion** — Summary and implications\n"
            "5. **Sources** — Types of sources consulted\n\n"
            "Use clear headings, professional tone, and avoid speculation. "
            "Only include information that was actually found in the research."
        ),
        "model_ref": "@reasoning",
        # Slightly lower temperature for more deterministic reporting
        "temperature": 0.3,
    },
]

# Workflow-level error handling: continue to next step on failure,
# or stop the workflow entirely
# on_error defaults to "stop" for critical workflows

WORKFLOW_DEFINITION = {
    "key": MAF_KEY,
    "name": NAME,
    "description": DESCRIPTION,
    "steps": [
        {
            **step,
            # Validate step fields are complete
            "on_error": "stop",
        }
        for step in STEPS
    ],
}
