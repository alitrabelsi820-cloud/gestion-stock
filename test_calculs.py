#!/usr/bin/env python3
"""
Tests automatisés des calculs critiques (argent).
Lancer :  python3 test_calculs.py
Aucune dépendance externe — exécute toutes les fonctions test_*.
"""
import app


def test_is_service():
    assert app._is_service({"type_vente": "service"}) is True
    assert app._is_service({"type_vente": "produit"}) is False
    assert app._is_service({}) is False


def test_ventes_stats_separe_produit_service():
    ventes = [
        {"date_vente": "2026-05-01", "pv": 100, "benef": 40, "or_grs": 1,
         "type_vente": "produit", "client": "A"},
        {"date_vente": "2026-05-02", "pv": 500, "benef": 300, "or_grs": 0,
         "type_vente": "service", "client": "B"},
    ]
    s = app.ventes_stats(ventes)
    assert s["ca"] == 100,            f"CA produits attendu 100, obtenu {s['ca']}"
    assert s["benef"] == 40,          f"Bénéf produits attendu 40, obtenu {s['benef']}"
    assert s["ca_service"] == 500,    f"CA service attendu 500, obtenu {s['ca_service']}"
    assert s["benef_service"] == 300, f"Bénéf service attendu 300"
    assert s["ca_total"] == 600,      f"CA total attendu 600, obtenu {s['ca_total']}"
    assert s["benef_total"] == 340,   f"Bénéf total attendu 340"


def test_ventes_stats_filtre_dates():
    ventes = [
        {"date_vente": "2026-04-30", "pv": 100, "benef": 50, "type_vente": "produit"},
        {"date_vente": "2026-05-15", "pv": 200, "benef": 80, "type_vente": "produit"},
    ]
    s = app.ventes_stats(ventes, date_from="2026-05-01", date_to="2026-05-31")
    assert s["ca"] == 200, f"Filtre date : CA attendu 200, obtenu {s['ca']}"


def test_detect_anomalies():
    ventes = [
        {"ref": 1, "pv": 70000, "pa": 50000, "benef": 20000, "type_vente": "produit"},  # OK
        {"ref": 2, "pv": 10000, "pa": 50000, "benef": -40000, "type_vente": "produit"}, # perte
        {"ref": 3, "pv": 1,     "pa": 0,     "benef": 1,      "type_vente": "produit"},  # pv suspect
        {"ref": 4, "pv": 30000, "pa": 30000, "benef": 0,      "type_vente": "produit"},  # marge nulle
        {"ref": 5, "pv": 90000, "pa": 5000,  "benef": 85000,  "type_vente": "produit"},  # marge haute
        {"ref": 6, "pv": 99999, "pa": 1,     "benef": 99998,  "type_vente": "service"},  # service exclu
    ]
    a = app.detect_anomalies(ventes)
    refs = {x["ref"] for x in a}
    assert 1 not in refs, "Vente saine ne doit pas être signalée"
    assert {2, 3, 4, 5}.issubset(refs), f"Anomalies manquantes : {refs}"
    assert 6 not in refs, "Les services doivent être exclus de la détection"


def test_monthly_stats():
    ventes = [
        {"date_vente": "2026-05-01", "pv": 100, "benef": 40, "or_grs": 2, "type_vente": "produit"},
        {"date_vente": "2026-05-20", "pv": 300, "benef": 100, "or_grs": 1, "type_vente": "service"},
    ]
    m = app.monthly_stats(ventes)
    mai = next(x for x in m if x["mois"] == "2026-05")
    assert mai["ca"] == 100,         f"CA mai produits attendu 100, obtenu {mai['ca']}"
    assert mai["ca_service"] == 300, f"CA mai service attendu 300"
    assert mai["ca_total"] == 400,   f"CA total mai attendu 400"


def test_pwd_match():
    assert app._pwd_match("abc", "abc") is True
    assert app._pwd_match("abc", "abd") is False
    assert app._pwd_match("", "secret") is False


# ── Runner ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    ok = 0
    fail = 0
    for t in tests:
        try:
            t()
            print(f"  ✅ {t.__name__}")
            ok += 1
        except AssertionError as e:
            print(f"  ❌ {t.__name__} — {e}")
            fail += 1
        except Exception as e:
            print(f"  ⚠️  {t.__name__} — erreur : {e}")
            fail += 1
    print(f"\n{ok} réussi(s), {fail} échec(s) sur {len(tests)} tests.")
    raise SystemExit(1 if fail else 0)
