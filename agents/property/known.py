"""Verified starting points for some cities. The agent queries these first; everywhere
else it finds datasets and layers itself with find_datasets / find_layers.

Each hint names the source and how its address columns are written, so the agent can
build a working filter on the first try. Checked live on 2026-10-25.
"""

CITY_HINTS = {
    ("NY", "new york"): [
        "Socrata data.cityofnewyork.us wvxf-dwi5 HPD Housing Maintenance Code Violations: housenumber, streetname (e.g. 'REMSEN AVENUE'), zip, apartment, class (A/B/C), novdescription, inspectiondate, currentstatus, violationstatus.",
        "Socrata data.cityofnewyork.us ygpa-z7cr HPD complaints and problems: house_number, street_name, post_code, apartment, received_date, major_category, minor_category, complaint_status.",
        "Socrata data.cityofnewyork.us tesw-yqqr HPD building registrations: housenumber, streetname, zip, registrationid. Then feu5-w2e2 registration contacts by registrationid: type, corporationname, firstname, lastname.",
        "Socrata data.cityofnewyork.us eabe-havv DOB complaints received: house_number, house_street, zip_code, complaint_category, status, date_entered.",
    ],
    ("IL", "chicago"): [
        "Socrata data.cityofchicago.org 22u3-xenr Building Violations: street_number, street_direction, street_name (e.g. 'CICERO'), street_type, violation_date, violation_code, violation_status, violation_description, inspection_status, department_bureau.",
    ],
    ("CA", "oakland"): [
        "Socrata data.oaklandca.gov quth-gb8e OAK311 service requests: probaddress (e.g. '7951 MACARTHUR BLVD'), description ('Code Enforcement', 'Animal and Insect Control', ...), datetimeinit, status.",
        "ArcGIS https://services.arcgis.com/9tC74aDHuml0x5Yz/arcgis/rest/services/Oakland_Parcels_Public/FeatureServer/0 parcels: FULL_SITUSADDRESS (e.g. '381 8TH ST OAKLAND 94607'), APN_GROUND, FULL_OWNERADDRESS (owner mailing address, no owner name).",
        "Accela agency OAKLAND: module Enforcement for code-enforcement cases and Building for permits, searched by house number and street name without suffix (e.g. 7951, MACARTHUR).",
    ],
    ("OH", "cincinnati"): [
        "Socrata data.cincinnati-oh.gov cncm-znd6 Code Enforcement: full_address (e.g. '4359 RIDGEVIEW AV'), comp_type_desc, sub_type_desc, entered_date, data_status_display.",
    ],
}


def hints_for(geo):
    if not geo:
        return []
    keys = {(geo.get("state", ""), (geo.get("city") or "").lower()), (geo.get("state", ""), (geo.get("postal_city") or "").lower())}
    return [h for k in keys for h in CITY_HINTS.get(k, [])]
