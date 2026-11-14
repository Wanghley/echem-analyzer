"""The one-page datasheet of each sensor variant."""
import pytest
from PySide6.QtPdf import QPdfDocument

from echem_analyzer import acceptance as acc
from echem_analyzer import datasheet as ds
from echem_analyzer import devices as dv
from echem_analyzer.models import ProjectData, ProjectMeta
from echem_analyzer.report import FigureBlock, KeyValueBlock, NoteBlock
from echem_analyzer.report_pdf import write_pdf
from test_variants import batch


def project():
    rs, devs = batch()
    extras: dict = {}
    dv.save(extras, devs)
    acc.save(extras, [acc.Rule("Peak", minimum=3.0)])
    return ProjectData(meta=ProjectMeta(name="Ink study", analyte="dopamine", operator="W"), responses=rs, extras=extras)


def kv(section, caption):
    return dict(next(b for b in section.blocks if isinstance(b, KeyValueBlock) and b.caption == caption).rows)


def test_each_variant_gets_a_page_with_how_it_was_made_and_how_it_performs():
    rep = ds.build(project(), "Peak")
    assert [s.title for s in rep.sections] == ["Datasheet · thick", "Datasheet · thin"] and rep.title == "Sensor datasheet · Peak"
    assert dict(rep.meta_rows) == {"Project": "Ink study", "Analyte": "dopamine", "Operator": "W"}          # empty fields are left out
    thin = rep.sections[1]
    made = kv(thin, "How it was made")
    assert made["Electrodes"] == "3: T1, T2, T3" and made["Lot"] == "L1" and made["anneal"] == "120 °C"
    assert made["ink volume (µL)"].startswith("2.1") and "±" in made["ink volume (µL)"]                  # 2.0, 2.1, 2.2: mean ± sd
    perf = kv(thin, "Performance")
    assert perf["Sensitivity, mean of electrodes"].startswith("0.5") and "µA/µM" in perf["Sensitivity, mean of electrodes"]
    assert perf["Spread between electrodes"].endswith("(3 calibrated)") and perf["Linear range"].endswith("µM")
    assert perf["Acceptance yield"].startswith("100 % of 3 judged") and perf["Response time t90 (median)"] == "—"
    figs = [b for b in thin.blocks if isinstance(b, FigureBlock)]
    assert len(figs) == 1 and figs[0].png.startswith(b"\x89PNG") and "thin" in figs[0].caption
    notes = " ".join(b.text for b in thin.blocks if isinstance(b, NoteBlock))
    assert "from 1 to 20 µM (5 levels)" in notes and "k = 3" in notes


def test_a_single_variant_can_be_asked_for_and_nothing_to_show_is_an_error():
    rep = ds.build(project(), "Peak", variant_names=["thick"])
    assert [s.title for s in rep.sections] == ["Datasheet · thick"]
    with pytest.raises(ValueError, match="no variant named"):
        ds.build(project(), "Peak", variant_names=["missing"])
    with pytest.raises(ValueError, match="Nothing to put on a datasheet"):
        ds.build(ProjectData(), "Peak")


@pytest.mark.parametrize("settings", [{"page_size": "A4"}, {"page_size": "Letter"},
                                      {"page_size": "Letter", "plot_style": "publication", "plot_font_scale": 1.07}])    # the last is a real user's
def test_the_pdf_has_one_page_per_variant_on_either_paper_size_and_style(qapp, tmp_path, settings):
    from echem_analyzer.prefs import prefs
    before = {k: prefs.get(k) for k in settings}
    for k, v in settings.items():
        prefs.set(k, v, save=False)
    try:
        pages = write_pdf(ds.build(project(), "Peak"), tmp_path / "datasheet.pdf")
    finally:
        for k, v in before.items():
            prefs.set(k, v, save=False)
    assert pages == 2                                                                           # one page each, as promised, even on shorter paper
    doc = QPdfDocument()
    doc.load(str(tmp_path / "datasheet.pdf"))
    assert doc.pageCount() == 2 and (tmp_path / "datasheet.pdf").stat().st_size > 20_000


def test_the_variants_tab_exports_the_datasheet_and_explains_when_it_cannot(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    from helpers import make_window
    win = make_window(tmp_path / "w")
    panel = win.study_tab.variants
    said = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a: said.append(a[-1])))
    assert not panel.export_datasheet() and said and "Nothing to put on a datasheet" in said[0]              # an empty project
    data = project()
    win.session.responses[:] = data.responses
    win.session.extras.update(data.extras)
    win.session.responses_changed.emit()
    out = tmp_path / "sheet.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), "")))
    assert panel.export_datasheet() and out.stat().st_size > 20_000
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", "")))
    assert not panel.export_datasheet()                                                                       # cancelled: nothing written
