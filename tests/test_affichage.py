"""Tests de ce qui s'affiche sous chaque article : extrait, orientation, et tri du fil.

Trois choses ajoutées ensemble parce qu'elles répondent à la même question — « de quoi
parle cet article, qui le dit, et qu'est-ce qui compte aujourd'hui ? » — mais qui n'ont
rien de commun techniquement.
"""
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "collecteur"))
sys.path.insert(0, str(RACINE))
import base  # noqa: E402
import collect  # noqa: E402
from serveur import app as serveur  # noqa: E402


def poser(cx, titre, source, resume="", lien=None, theme="def"):
    a = {"titre": titre, "lien": lien or f"https://x.test/{abs(hash(titre + source)) % 10**9}",
         "date": base.maintenant_utc(), "source": source, "resume": resume,
         "zone": "eur", "theme": theme, "langue": "fr", "theme_force": True}
    etat = base.enregistrer(cx, a, collect.normaliser(titre), base.maintenant_utc(), None)
    cx.commit()
    return etat


@pytest.fixture
def cx(tmp_path):
    c = base.ouvrir(tmp_path / "map.sqlite")
    yield c
    c.close()


# ---------- extrait ----------
def test_les_premieres_phrases_seulement():
    texte = ("Un hôpital a dérouté ses urgences. Le rançongiciel a chiffré les serveurs. "
             "La direction parle de plusieurs jours. Cette quatrième phrase est de trop.")
    court = base.extrait_court(texte)
    assert court.endswith("plusieurs jours.")
    assert "quatrième" not in court


def test_une_phrase_sans_point_passe_entiere():
    assert base.extrait_court("Une seule phrase sans point final") == \
        "Une seule phrase sans point final"


def test_un_chapo_trop_long_est_coupe_proprement():
    court = base.extrait_court("mot " * 300)
    assert len(court) <= base.LONGUEUR_EXTRAIT + 1 and court.endswith("…")
    assert not court.endswith("mo…"), "la coupe se fait sur un espace, pas au milieu d'un mot"


@pytest.mark.parametrize("vide", ["", "   ", None])
def test_sans_chapo_pas_d_extrait(vide):
    assert base.extrait_court(vide) == ""


def test_l_extrait_est_enregistre_et_servi(cx):
    poser(cx, "Un titre", "Le Monde", "Le chapô du média. Deuxième phrase.")
    ligne = cx.execute("SELECT extrait FROM articles").fetchone()
    assert ligne["extrait"] == "Le chapô du média. Deuxième phrase."


def test_un_flux_sans_chapo_ne_met_rien(cx):
    poser(cx, "Un titre nu", "CERT-FR", "")
    assert cx.execute("SELECT extrait FROM articles").fetchone()["extrait"] is None


def test_un_article_deja_connu_recupere_son_extrait(cx):
    """L'extrait est arrivé en v18 : sans ce rattrapage, le fonds resterait nu trente jours."""
    poser(cx, "Déjà là", "Le Monde", "", lien="https://x.test/meme")
    assert cx.execute("SELECT extrait FROM articles").fetchone()["extrait"] is None
    poser(cx, "Déjà là", "Le Monde", "Le chapô, enfin.", lien="https://x.test/meme")
    assert cx.execute("SELECT extrait FROM articles").fetchone()["extrait"] == "Le chapô, enfin."


def test_un_extrait_existant_n_est_pas_ecrase(cx):
    """Si le média a changé son chapô, le sien fait foi — on ne le réécrit pas en boucle."""
    poser(cx, "Déjà là", "Le Monde", "Le premier chapô.", lien="https://x.test/meme")
    poser(cx, "Déjà là", "Le Monde", "Un autre chapô.", lien="https://x.test/meme")
    assert cx.execute("SELECT extrait FROM articles").fetchone()["extrait"] == "Le premier chapô."


# ---------- orientation éditoriale ----------
def test_le_fichier_livre_est_valide():
    o = base.charger_orientations()
    assert o["actif"] is True and len(o["sources"]) > 40
    declarees = {f["nom"] for f in base.lire_config(base.FLUX_JSON, "flux")}
    manquantes = declarees - set(o["sources"])
    assert not manquantes, f"sources de flux.json sans orientation : {sorted(manquantes)}"


def test_chaque_entree_porte_une_etiquette_et_une_justification():
    for nom, s in base.charger_orientations()["sources"].items():
        assert s["etiquette"], nom
        assert len(s["pourquoi"]) > 20, f"{nom} : dire sur quoi repose le jugement"


def test_ce_qui_n_a_pas_de_place_sur_l_axe_n_en_recoit_pas():
    """Un axe gauche-droite français ne s'applique ni à une agence de cybersécurité, ni au
    service public, ni à un think tank américain. Leur en inventer une position serait pire
    que de n'en donner aucune."""
    sources = base.charger_orientations()["sources"]
    for nom in ("CERT-FR (ANSSI)", "Foreign Affairs", "franceinfo", "RUSI", "RFI",
                "Marianne", "20 Minutes", "Courrier international"):
        assert sources[nom]["position"] is None, nom


def test_l_axe_est_renseigne_et_couvre_tout_le_spectre():
    """Un repère qui ne classerait que quelques titres d'un seul bord ne vaudrait rien."""
    positions = [s["position"] for s in base.charger_orientations()["sources"].values()
                 if s["position"] is not None]
    assert len(positions) >= 15
    assert min(positions) <= -60 and max(positions) >= 60, "les deux extrémités sont tenues"
    gauche = [p for p in positions if p < -10]
    droite = [p for p in positions if p > 10]
    assert gauche and droite
    assert 0.4 <= len(gauche) / (len(gauche) + len(droite)) <= 0.6, \
        f"répartition déséquilibrée : {len(gauche)} à gauche, {len(droite)} à droite"


def ecrire_orientations(tmp_path, **champs):
    f = tmp_path / "orientations.json"
    f.write_text(json.dumps(champs, ensure_ascii=False), encoding="utf-8")
    return f


def test_fichier_absent_desactive_le_repere(tmp_path):
    assert base.charger_orientations(tmp_path / "rien.json")["actif"] is False


def test_les_lignes_de_commentaire_sont_ignorees(tmp_path):
    f = ecrire_orientations(tmp_path, sources={"_note": "un commentaire",
                                               "M": {"position": 0, "etiquette": "Centre"}})
    assert list(base.charger_orientations(f)["sources"]) == ["M"]


@pytest.mark.parametrize("position", [-101, 101, "gauche", [0]])
def test_position_hors_axe_refusee(tmp_path, position):
    f = ecrire_orientations(tmp_path, sources={"M": {"position": position, "etiquette": "x"}})
    with pytest.raises(ValueError, match="entre -100 et 100"):
        base.charger_orientations(f)


def test_etiquette_obligatoire(tmp_path):
    f = ecrire_orientations(tmp_path, sources={"M": {"position": 0}})
    with pytest.raises(ValueError, match="étiquette"):
        base.charger_orientations(f)


# ---------- API et site ----------
@pytest.fixture
def client(cx, monkeypatch, tmp_path):
    poser(cx, "Frappes sur Kharkiv", "Le Monde", "Le chapô du Monde.")
    poser(cx, "Frappes sur Kharkiv", "France 24", "Celui de France 24.",
          lien="https://x.test/f24")
    poser(cx, "Un sujet isolé", "ZATAZ", "", theme="cyb")
    monkeypatch.setattr(serveur, "BASE", tmp_path / "map.sqlite")
    serveur._referentiel = None
    yield TestClient(serveur.app)
    serveur._referentiel = None


def test_l_api_sert_l_extrait(client):
    a = client.get("/api/articles", params={"grouper": False}).json()["articles"]
    extraits = {x["source"]: x["extrait"] for x in a}
    assert extraits["Le Monde"] == "Le chapô du Monde."
    assert extraits["ZATAZ"] is None


def test_l_api_sert_les_orientations(client):
    o = client.get("/api/referentiel").json()["orientations"]
    assert o["sources"]["Le Monde"]["position"] == -20
    assert o["sources"]["franceinfo"]["position"] is None
    assert o["axe"]["reperes"][0]["nom"] == "Gauche"


def test_le_repere_peut_etre_coupe(client, monkeypatch, tmp_path):
    monkeypatch.setattr(base, "ORIENTATIONS_JSON",
                        ecrire_orientations(tmp_path, actif=False, sources={}))
    serveur._referentiel = None
    assert client.get("/api/referentiel").json()["orientations"] is None


def test_un_fichier_casse_ne_rend_pas_le_site_inaccessible(client, monkeypatch, tmp_path):
    monkeypatch.setattr(base, "ORIENTATIONS_JSON",
                        ecrire_orientations(tmp_path, sources={"M": {"position": 999}}))
    serveur._referentiel = None
    r = client.get("/api/referentiel")
    assert r.status_code == 200 and r.json()["orientations"] is None


def test_le_front_affiche_extrait_et_orientation(client):
    page = client.get("/").text
    assert 'class="extrait"' in page and "function orientation(" in page
    assert "Orientation non renseignée" in page
    # le sens ne doit pas reposer sur la seule couleur
    assert "aria-label=" in page and "Ligne éditoriale de" in page


# ---------- tri ----------
def test_le_tri_par_reprises_fait_remonter_les_sujets_cites(client):
    repris = client.get("/api/articles", params={"tri": "reprises"}).json()
    assert repris["tri"] == "reprises"
    assert repris["articles"][0]["nb_sources"] == 2
    comptes = [a["nb_sources"] for a in repris["articles"]]
    assert comptes == sorted(comptes, reverse=True)


def test_le_tri_par_defaut_reste_chronologique(client):
    d = client.get("/api/articles").json()
    assert d["tri"] == "date"
    dates = [a["date"] for a in d["articles"]]
    assert dates == sorted(dates, reverse=True)


def test_un_tri_inconnu_est_refuse(client):
    assert client.get("/api/articles", params={"tri": "popularite"}).status_code == 422


def test_le_front_propose_le_tri(client):
    page = client.get("/").text
    assert 'data-tri="reprises"' in page and "setTri" in page
