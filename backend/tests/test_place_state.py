"""The state beside a lead's city: validated, derived from the place, or nothing (pure, no database)."""
import pytest

from app.services import place_state as ps

STATES_AND_UTS = 36


def test_real_states_in_any_spelling_come_out_in_full():
    for raw, want in [("Tamil Nadu", "Tamil Nadu"), ("tamilnadu", "Tamil Nadu"), ("TN", "Tamil Nadu"), ("T.N.", "Tamil Nadu"),
                      ("tamil_nadu", "Tamil Nadu"), ("  TAMIL   NADU ", "Tamil Nadu"), ("Tamil Nadu, India", "Tamil Nadu"),
                      ("KL", "Kerala"), ("kerala", "Kerala"), ("Kerela", "Kerala"), ("AP", "Andhra Pradesh"), ("Andhra", "Andhra Pradesh"),
                      ("TS", "Telangana"), ("Orissa", "Odisha"), ("J&K", "Jammu and Kashmir"), ("New Delhi", "Delhi"),
                      ("Pondicherry", "Puducherry"), ("MH", "Maharashtra"), ("Uttaranchal", "Uttarakhand"), ("UP", "Uttar Pradesh"),
                      ("Pollachi, Tamil Nadu", "Tamil Nadu")]:
        assert ps.clean_state(raw) == want, raw


def test_every_state_and_union_territory_is_known():
    assert len(ps.known_states()) == STATES_AND_UTS
    for name in ps.known_states():
        assert ps.clean_state(name) == name


def test_countries_and_regions_the_sheets_carry_are_kept():
    for raw, want in [("UAE", "United Arab Emirates"), ("Dubai", "United Arab Emirates"), ("Qatar", "Qatar"), ("KSA", "Saudi Arabia"),
                      ("Saudi Arabia", "Saudi Arabia"), ("Kuwait", "Kuwait"), ("Oman", "Oman"), ("Sri Lanka", "Sri Lanka"), ("USA", "United States")]:
        assert ps.clean_state(raw) == want, raw


@pytest.mark.parametrize("junk", ["H", "h", "31", "1", "123456", "ld_1795", "AB12", "x", "", "  ", None, "India", "NA", "n/a", "null", "Male", "Female",
                                  "zz", "Some Random Words", "9876543210", "ab"])
def test_junk_is_not_a_state(junk):
    assert ps.clean_state(junk) is None


def test_the_city_repeated_is_not_a_state():
    assert ps.clean_state("Chennai", "Chennai") is None
    assert ps.clean_state("pollachi ", " Pollachi") is None
    assert ps.clean_state("Kerala", "Chennai") == "Kerala"


def test_the_built_in_map_knows_the_places_of_the_lead_data():
    for city, want in [("Pollachi", "Tamil Nadu"), ("Chennai", "Tamil Nadu"), ("Coimbatore", "Tamil Nadu"), ("Pathanamthitta", "Kerala"),
                       ("Mayiladuthurai", "Tamil Nadu"), ("Thanjavur", "Tamil Nadu"), ("Kottayam", "Kerala"), ("Wayanad", "Kerala"),
                       ("Bengaluru", "Karnataka"), ("Bangalore", "Karnataka"), ("Hyderabad", "Telangana"), ("Visakhapatnam", "Andhra Pradesh"),
                       ("Mumbai", "Maharashtra"), ("Pune", "Maharashtra"), ("Delhi", "Delhi"), ("Ahmedabad", "Gujarat"), ("Ludhiana", "Punjab"),
                       ("Kolkata", "West Bengal"), ("Jaipur", "Rajasthan"), ("Lucknow", "Uttar Pradesh"), ("Bhubaneswar", "Odisha"),
                       ("Chennai - 600001", "Tamil Nadu"), ("Kottayam District", "Kerala"), ("  TRICHY ", "Tamil Nadu"), ("Pollachi, Tamil Nadu", "Tamil Nadu")]:
        assert ps.builtin_state(city) == want, city
    assert ps.builtin_state("Qwertyville") is None and ps.builtin_state("") is None and ps.builtin_state(None) is None


def test_the_built_in_map_is_large_and_every_target_is_a_real_state():
    assert len(ps._CITY_STATE) >= 600
    assert set(ps._CITY_STATE.values()) <= set(ps.known_states())
    assert {"Tamil Nadu", "Kerala", "Karnataka", "Andhra Pradesh", "Telangana", "Maharashtra", "Delhi", "Gujarat", "Punjab"} <= set(ps._CITY_STATE.values())


def test_the_most_common_valid_state_of_a_city_wins_and_junk_is_not_counted():
    found = ps.most_common_state_by_city([
        ("Kuttiyadi", "Kerala", 3), ("kuttiyadi ", "KL", 2), ("Kuttiyadi", "Tamil Nadu", 4), ("Kuttiyadi", "31", 90), ("Kuttiyadi", "H", 50),
        ("Qwertyville", "H", 10), ("Tie town", "Kerala", 1), ("Tie town", "Goa", 1), ("Dubai", "UAE", 5), ("Chennai", "Chennai", 7)])
    assert found == {"kuttiyadi": "Kerala", "tie town": "Goa"}   # 5 for Kerala beats 4; a tie goes to the first A-Z; countries and the city repeated are no answer


def test_display_state_prefers_the_stored_state_then_the_data_then_the_map_then_nothing():
    data = {"kuttiyadi": "Kerala"}
    assert ps.display_state("Pollachi", "H") == "Tamil Nadu"
    assert ps.display_state("Chennai", "31") == "Tamil Nadu"
    assert ps.display_state("Kottayam", "Tamilnadu") == "Tamil Nadu"   # what the lead says is kept, even if the place disagrees
    assert ps.display_state("Kuttiyadi", "7", data) == "Kerala"
    assert ps.display_state("Kuttiyadi", None, None) is None
    assert ps.display_state("Chennai", "Chennai", {"chennai": "Kerala"}) == "Kerala"   # the data map outranks the built-in one
    assert ps.display_state("Qwertyville", "H", data) is None
    assert ps.display_state(None, "H") is None and ps.display_state(None, "TN") == "Tamil Nadu"
    assert ps.display_state("Delhi", "Delhi") is None   # the city repeated, nothing more to say
    assert ps.display_state("Zzz", "9", None) is None   # nothing is guessed from anything else


def test_a_value_is_never_made_up():
    for city in ["Qwertyville", "H", "31", "x", "Unknown", "NA"]:
        assert ps.display_state(city, "H") is None, city


@pytest.mark.parametrize("raw, want", [("Pollachi", "Pollachi"), ("  chennai   ", "Chennai"), ("POLLACHI", "Pollachi"), ("tiruchirappalli  city", "Tiruchirappalli  City".replace("  ", " ")),
                                       ("UAE", "UAE"), ("McLeodganj", "McLeodganj"), ("Delhi", "Delhi"), ("Goa", "Goa"), ("New   Delhi", "New Delhi"),
                                       ("Kochi (Ernakulam)", "Kochi (Ernakulam)"), ("Testville 3", "Testville 3"), (" - Salem - ", "Salem")])
def test_clean_city_tidies_a_real_city(raw, want):
    assert ps.clean_city(raw) == want


@pytest.mark.parametrize("junk", ["H", "h", "31", "600001", "  ", "", None, "N/A", "n/a", "null", "NULL", "-", "--", ".", "none", "Unknown", "x",
                                  "Tamil Nadu", "KERALA", "karnataka"])
def test_clean_city_drops_junk_and_a_bare_state_name(junk):
    assert ps.clean_city(junk) is None
