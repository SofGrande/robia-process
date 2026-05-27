"""Tests para reglas de clasificación (tópico + naturaleza), lógica pura."""
from __future__ import annotations

import unittest

from robia_procesos.core import topicos_catalogo as cat
from robia_procesos.core.contrato import Confianza, Resultado
from robia_procesos.reglas.clasificacion_naturaleza import (
    _evaluar_completitud as nat_completitud,
    _evaluar_multinaturaleza as nat_multi,
    _evaluar_validez as nat_validez,
)
from robia_procesos.core.geografia import Geografia
from robia_procesos.reglas.clasificacion_topico import (
    _evaluar_completitud as top_completitud,
    _evaluar_geografia_consistente as top_geo,
    _evaluar_multitopico as top_multi,
    _evaluar_validez as top_validez,
)


def _catalogo_fixture() -> cat.Catalogo:
    return cat._construir_desde_filas(
        combinaciones=[
            ("AR", "", "Online", "Configuraciones Online", "Idiomas Y Monedas"),
            ("AR", "", "Pago Nube", "Cuenta", "Cuotas - Pago Nube"),
        ],
        naturalezas=[
            ("LATAM", "Duda Autoatención"),
            ("LATAM", "Issue"),
            ("LATAM", "Problem"),
        ],
    )


class TopicoCompletitudTests(unittest.TestCase):
    def test_tripla_completa_es_thumbs_up(self) -> None:
        res = top_completitud(1, ("Online", "Configuraciones Online", "Idiomas Y Monedas", "raw"))
        self.assertIs(res.resultado, Resultado.THUMBS_UP)
        self.assertIs(res.confianza, Confianza.DIRECTA)

    def test_subtopico_vacio_es_thumbs_down(self) -> None:
        res = top_completitud(2, ("Online", "Configuraciones Online", "", "raw"))
        self.assertIs(res.resultado, Resultado.THUMBS_DOWN)
        self.assertIn("subtopico", res.nota or "")

    def test_main_y_sec_vacios_listados_en_nota(self) -> None:
        res = top_completitud(3, ("", "", "Idiomas Y Monedas", ""))
        self.assertIs(res.resultado, Resultado.THUMBS_DOWN)
        self.assertIn("main", res.nota or "")
        self.assertIn("secundario", res.nota or "")

    def test_sin_filas_es_no_evaluable(self) -> None:
        res = top_completitud(4, None)
        self.assertIs(res.resultado, Resultado.NO_EVALUABLE)


class TopicoValidezTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalogo = _catalogo_fixture()

    def test_tripla_en_catalogo_es_thumbs_up(self) -> None:
        res = top_validez(
            1, ("Online", "Configuraciones Online", "Idiomas Y Monedas", "raw"), self.catalogo
        )
        self.assertIs(res.resultado, Resultado.THUMBS_UP)
        self.assertIs(res.confianza, Confianza.PARCIAL)

    def test_tripla_inconsistente_es_thumbs_down_con_nota(self) -> None:
        # subtópico existe pero combinada con tópicos equivocados
        res = top_validez(
            2, ("Pago Nube", "Cuenta", "Idiomas Y Monedas", "raw"), self.catalogo
        )
        self.assertIs(res.resultado, Resultado.THUMBS_DOWN)
        self.assertIn("Subtópico sí existe", res.nota or "")

    def test_subtopico_inexistente(self) -> None:
        res = top_validez(
            3, ("Online", "Configuraciones Online", "Subtopico Falso", "raw"), self.catalogo
        )
        self.assertIs(res.resultado, Resultado.THUMBS_DOWN)
        self.assertIn("Subtópico no existe", res.nota or "")

    def test_sin_subtopico_es_no_evaluable(self) -> None:
        res = top_validez(4, ("Online", "Configuraciones Online", "", ""), self.catalogo)
        self.assertIs(res.resultado, Resultado.NO_EVALUABLE)


class NaturalezaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalogo = _catalogo_fixture()

    def test_completitud_thumbs_up(self) -> None:
        res = nat_completitud(1, "Issue")
        self.assertIs(res.resultado, Resultado.THUMBS_UP)

    def test_completitud_vacio_thumbs_down(self) -> None:
        res = nat_completitud(2, "")
        self.assertIs(res.resultado, Resultado.THUMBS_DOWN)

    def test_completitud_sin_fila_no_evaluable(self) -> None:
        res = nat_completitud(3, None)
        self.assertIs(res.resultado, Resultado.NO_EVALUABLE)

    def test_validez_directa(self) -> None:
        res = nat_validez(1, "Issue", self.catalogo)
        self.assertIs(res.resultado, Resultado.THUMBS_UP)

    def test_validez_via_alias_hibrido(self) -> None:
        # 'Duda/Dúvida Auto' debería resolver al canónico 'Duda Autoatención'
        res = nat_validez(2, "Duda/Dúvida Auto", self.catalogo)
        self.assertIs(res.resultado, Resultado.THUMBS_UP)

    def test_validez_invalida(self) -> None:
        res = nat_validez(3, "Inventada", self.catalogo)
        self.assertIs(res.resultado, Resultado.THUMBS_DOWN)


class MultitopicoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalogo = _catalogo_fixture()

    def test_sin_filas_no_evaluable(self) -> None:
        res = top_multi(1, None, self.catalogo)
        self.assertIs(res.resultado, Resultado.NO_EVALUABLE)

    def test_monotopico_no_evaluable(self) -> None:
        res = top_multi(
            2,
            [("Online", "Configuraciones Online", "Idiomas Y Monedas", "raw")],
            self.catalogo,
        )
        self.assertIs(res.resultado, Resultado.NO_EVALUABLE)
        self.assertIn("monotópico", res.nota or "")

    def test_multitopico_todas_validas(self) -> None:
        res = top_multi(
            3,
            [
                ("Online", "Configuraciones Online", "Idiomas Y Monedas", "raw1"),
                ("Pago Nube", "Cuenta", "Cuotas - Pago Nube", "raw2"),
            ],
            self.catalogo,
        )
        self.assertIs(res.resultado, Resultado.THUMBS_UP)
        self.assertEqual(len(res.evidencia), 2)

    def test_multitopico_alguna_invalida(self) -> None:
        res = top_multi(
            4,
            [
                ("Online", "Configuraciones Online", "Idiomas Y Monedas", "raw1"),
                ("Inventado", "Falso", "No Existe", "raw2"),
            ],
            self.catalogo,
        )
        self.assertIs(res.resultado, Resultado.THUMBS_DOWN)
        self.assertIn("1 de 2", res.nota or "")


class MultinaturalezaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalogo = _catalogo_fixture()

    def test_sin_filas_no_evaluable(self) -> None:
        res = nat_multi(1, None, self.catalogo)
        self.assertIs(res.resultado, Resultado.NO_EVALUABLE)

    def test_una_sola_no_evaluable(self) -> None:
        res = nat_multi(2, ["Issue"], self.catalogo)
        self.assertIs(res.resultado, Resultado.NO_EVALUABLE)
        self.assertIn("Una sola naturaleza", res.nota or "")

    def test_dos_validas(self) -> None:
        res = nat_multi(3, ["Issue", "Problem"], self.catalogo)
        self.assertIs(res.resultado, Resultado.THUMBS_UP)

    def test_alias_hibridos_dos_naturalezas(self) -> None:
        # mezcla nombre del lake con canónico — ambos deben validar
        res = nat_multi(4, ["Duda/Dúvida Auto", "Issue"], self.catalogo)
        self.assertIs(res.resultado, Resultado.THUMBS_UP)

    def test_alguna_invalida(self) -> None:
        res = nat_multi(5, ["Issue", "Inventada"], self.catalogo)
        self.assertIs(res.resultado, Resultado.THUMBS_DOWN)
        self.assertIn("1 de 2", res.nota or "")


class TopicoGeografiaTests(unittest.TestCase):
    """El catálogo tiene la misma tripla en hojas distintas para testear el filtro por geo."""

    def setUp(self) -> None:
        # tripla "X / Y / Z_AR" SOLO en hoja AR (LATAM); "A / B / C_BR" SOLO en BR
        self.catalogo = cat._construir_desde_filas(
            combinaciones=[
                ("AR_Tópicos_Zendesk/Slack", "", "X", "Y", "Z_AR"),
                ("[BR] Tópicos Zendesk/Slack", "", "A", "B", "C_BR"),
                ("LATAM_Tópicos_Zendesk/Slack", "", "Compartido", "Sec", "Sub"),
            ],
            naturalezas=[("LATAM", "Issue")],
        )

    def test_tripla_de_geo_correcta_thumbs_up(self) -> None:
        res = top_geo(
            1, ("X", "Y", "Z_AR", "raw"), Geografia.LATAM, self.catalogo
        )
        self.assertIs(res.resultado, Resultado.THUMBS_UP)

    def test_tripla_solo_en_otra_geo_thumbs_down(self) -> None:
        # ticket BR pero tripla solo en AR/LATAM
        res = top_geo(
            2, ("X", "Y", "Z_AR", "raw"), Geografia.BR, self.catalogo
        )
        self.assertIs(res.resultado, Resultado.THUMBS_DOWN)
        self.assertIn("otra geo", res.nota or "")

    def test_tripla_inexistente_no_evaluable(self) -> None:
        # ya cubierto por topico_combinacion_valida
        res = top_geo(
            3, ("Inventado", "Falso", "Nada", "raw"), Geografia.LATAM, self.catalogo
        )
        self.assertIs(res.resultado, Resultado.NO_EVALUABLE)
        self.assertIn("ninguna geo", res.nota or "")

    def test_geografia_desconocida_no_evaluable(self) -> None:
        res = top_geo(
            4, ("X", "Y", "Z_AR", "raw"), Geografia.DESCONOCIDA, self.catalogo
        )
        self.assertIs(res.resultado, Resultado.NO_EVALUABLE)
        self.assertIn("guru_name sin patrón", res.nota or "")

    def test_tripla_incompleta_no_evaluable(self) -> None:
        res = top_geo(5, ("X", "Y", "", "raw"), Geografia.LATAM, self.catalogo)
        self.assertIs(res.resultado, Resultado.NO_EVALUABLE)


if __name__ == "__main__":
    unittest.main()
