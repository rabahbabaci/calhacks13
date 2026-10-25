"""A saved walkthrough used when Claude is not called. Three hazards, Oakland pack, one open and one closed record."""
DISCLAIMER_HEALTH = "General public health information, not medical advice or a diagnosis."
DISCLAIMER_LEGAL = "General legal information, not legal advice. A photo or AI label is never an officer's determination."
COMPLAINT_URL = "https://www.oaklandca.gov/Planning-Building/Code-Enforcement-Services/Report-a-Property-Complaint"


def vision_report():
    return {
        "case_id": "case_demo",
        "rooms": [{"room": "bedroom"}, {"room": "kitchen"}],
        "hazards": [
            {
                "hazard": "dampness_mold",
                "label": "Visible dampness or possible mold growth",
                "location": "bedroom wall beside the window",
                "room": "bedroom",
                "severity": "high",
                "confidence": 0.5,
                "evidence_ids": ["video_1_t343s"],
                "bbox_frame": "video_1_t343s",
                "first_seen_s": 343,
                "observed_signs": ["dark staining along the lower wall"],
            },
            {
                "hazard": "lead_paint",
                "label": "Peeling or deteriorated paint (possible lead hazard)",
                "location": "bedroom sill below the sliding door",
                "room": "bedroom",
                "severity": "moderate",
                "confidence": 0.55,
                "evidence_ids": ["video_1_t461s"],
                "bbox_frame": "video_1_t461s",
                "first_seen_s": 461,
                "observed_signs": ["paint lifting on the sill"],
            },
            {
                "hazard": "dampness_mold",
                "label": "Visible dampness or possible mold growth",
                "location": "kitchen counter at the faucet",
                "room": "kitchen",
                "severity": "low",
                "confidence": 0.35,
                "evidence_ids": ["video_1_t285s"],
                "bbox_frame": "video_1_t285s",
                "first_seen_s": 285,
                "observed_signs": ["dark residue at the faucet base"],
            },
        ],
        "not_visible_from_video": ["carbon_monoxide: CO is invisible; check for a working CO alarm"],
    }


def health():
    return {
        "dampness_mold": {
            "hazard": "dampness_mold",
            "urgent": False,
            "health_risks": [{"text": "Damp indoor spaces are linked with coughing and wheezing.", "chunk_ids": [1]}],
            "precautions": [
                {"text": "Keep the area dry and let fresh air through the room.", "chunk_ids": [2]},
                {"text": "Ask for the repair in writing.", "chunk_ids": [3]},
            ],
            "see_a_doctor_if": [{"text": "See a clinician if a cough or wheeze starts or gets worse.", "chunk_ids": [4]}],
            "disclaimer": DISCLAIMER_HEALTH,
        },
        "lead_paint": {
            "hazard": "lead_paint",
            "urgent": False,
            "health_risks": [{"text": "Peeling paint in older homes can be a lead risk, especially for children.", "chunk_ids": [5]}],
            "precautions": [{"text": "Do not scrape or sand the paint yourself.", "chunk_ids": [6]}],
            "see_a_doctor_if": [{"text": "Ask a clinician about a lead test for children who spend time in the room.", "chunk_ids": [7]}],
            "disclaimer": DISCLAIMER_HEALTH,
        },
    }


def legal():
    return {
        "dampness_mold": {
            "hazard": "dampness_mold",
            "landlord_duty": [{"text": "Habitable rooms are to be kept free of dampness.", "source_ids": ["P1"]}],
            "tenant_options": [{"text": "A tenant can report the condition through the city's property complaint form.", "source_ids": ["C1"]}],
            "deadlines": [],
            "ask_legal_aid": [],
            "sources": [
                {"id": "P1", "cite": "Cal. Health & Safety Code 17920.3(a)(11)", "url": "https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?sectionNum=17920.3.&lawCode=HSC", "kind": "statute"},
                {"id": "C1", "cite": "Oakland property complaint (code enforcement)", "url": COMPLAINT_URL, "kind": "agency"},
            ],
            "cautions": ["A photo or AI label is never an officer's determination"],
            "disclaimer": DISCLAIMER_LEGAL,
        },
        "lead_paint": {
            "hazard": "lead_paint",
            "landlord_duty": [{"text": "Paint that is peeling can be a lead hazard when a code officer finds it so.", "source_ids": ["P2"]}],
            "tenant_options": [{"text": "Report peeling paint through the same city complaint form.", "source_ids": ["C1"]}],
            "deadlines": [],
            "ask_legal_aid": [],
            "sources": [
                {"id": "P2", "cite": "Cal. Health & Safety Code 17920.10(a)", "url": "https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?lawCode=HSC&sectionNum=17920.10", "kind": "statute"},
                {"id": "C1", "cite": "Oakland property complaint (code enforcement)", "url": COMPLAINT_URL, "kind": "agency"},
            ],
            "cautions": ["A photo or AI label is never an officer's determination"],
            "disclaimer": DISCLAIMER_LEGAL,
        },
    }


def property_report(address):
    return {
        "address": address,
        "owner": {"name": "Example Owner LLC", "role": "owner", "mailing_address": "1 Mail St, Oakland, CA"},
        "manager": None,
        "records": [
            {
                "id": "R1",
                "kind": "violation",
                "title": "Dampness in a habitable room",
                "date": "2024-03-02",
                "status": "open",
                "detail": "Inspection noted damp walls.",
                "ref": "2601",
            },
            {
                "id": "R2",
                "kind": "violation",
                "title": "Plumbing leak under a sink",
                "date": "2023-01-11",
                "status": "abated",
                "detail": "Owner replaced the leaking supply line.",
                "ref": "1988",
            },
        ],
        "disclaimer": "Compiled from public records, which can be incomplete.",
    }
