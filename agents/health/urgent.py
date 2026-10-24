"""Hard-coded urgent path. No LLM, no corpus. Runs before anything else.

Any hazard in URGENT gets a fixed safety message and an output object that
matches docs/contracts.md section 5 with empty claim lists.
"""

PGE_GAS = "1-877-660-6789"

URGENT = {
    "gas_leak": (
        "If you smell gas or hear hissing, leave the building right away. Do not use "
        "light switches, phones, or anything that can spark while inside. From outside, "
        f"call 911 and PG&E's 24-hour gas line at {PGE_GAS}."
    ),
    "carbon_monoxide": (
        "If a carbon monoxide alarm is sounding or anyone feels dizzy, nauseous, or has a "
        "headache indoors, get everyone outside to fresh air right away and call 911. "
        "Do not go back inside until emergency responders say it is safe."
    ),
    "exposed_wiring": (
        "Do not touch exposed wires, outlets, or anything metal near them. Keep children "
        "and pets away. If there is sparking, burning smell, or smoke, leave and call 911. "
        "Otherwise turn off the circuit at the breaker if you can do so safely and report "
        "it to your landlord in writing today."
    ),
}

DISCLAIMER = "General public health information, not medical advice or a diagnosis."


def is_urgent(hazard):
    return hazard in URGENT


def urgent_assessment(hazard):
    """Section 5 output for an urgent hazard. Claim lists stay empty on purpose."""
    if hazard not in URGENT:
        raise ValueError(f"{hazard} is not an urgent hazard")
    return {
        "hazard": hazard,
        "urgent": True,
        "urgent_message": URGENT[hazard],
        "exposure_pathways": [],
        "health_risks": [],
        "vulnerable_groups": [],
        "precautions": [],
        "see_a_doctor_if": [],
        "sources": [],
        "disclaimer": DISCLAIMER,
    }
