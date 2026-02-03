from app.services.resident_lookup import find_best_match, extract_email_local


def test_fuzzy_match_email_local_to_name():
    target = extract_email_local("mbooreni@ttuhsc.edu")
    candidates = ["M. Boorenie", "Jane Doe"]
    assert find_best_match(target, candidates) == "M. Boorenie"
