"""Tests unitarios para la regla `cierre_coherente`.

No dependen de Databricks: testean la lógica pura llamando al evaluador
interno con timelines fabricadas. Para smoke-test contra el lake real, usar:

    python -m robia_procesos.cli evaluar 7253209

Correr los tests:

    python -m unittest tests.test_estado_conversacion -v
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from robia_procesos.core.contrato import Confianza, Resultado
from robia_procesos.reglas.estado_conversacion import (
    _detectar_tramos_hold,
    _evaluar_cierre_coherente,
    _evaluar_hold_sin_side_conv,
    _evaluar_pending_post_solved,
)


def _t(n: int) -> datetime:
    return datetime(2026, 4, 1, 10, 0, 0) + timedelta(minutes=n)


class CierreCoherenteTests(unittest.TestCase):
    def test_cierre_correcto_da_thumbs_up(self) -> None:
        eventos = [
            (_t(0), "new"),
            (_t(1), "open"),
            (_t(10), "pending"),
            (_t(60), "solved"),
            (_t(1440), "closed"),
        ]
        res = _evaluar_cierre_coherente(123, eventos)
        self.assertIs(res.resultado, Resultado.THUMBS_UP)
        self.assertIs(res.confianza, Confianza.DIRECTA)
        self.assertEqual(res.sub_regla, "cierre_coherente")

    def test_closed_sin_solved_da_thumbs_down(self) -> None:
        eventos = [
            (_t(0), "new"),
            (_t(1), "open"),
            (_t(10), "pending"),
            (_t(1440), "closed"),
        ]
        res = _evaluar_cierre_coherente(456, eventos)
        self.assertIs(res.resultado, Resultado.THUMBS_DOWN)
        self.assertIn("sin haber pasado por 'solved'", res.nota or "")

    def test_ticket_abierto_es_no_evaluable(self) -> None:
        eventos = [
            (_t(0), "new"),
            (_t(1), "open"),
            (_t(10), "pending"),
        ]
        res = _evaluar_cierre_coherente(789, eventos)
        self.assertIs(res.resultado, Resultado.NO_EVALUABLE)

    def test_sin_eventos_es_no_evaluable(self) -> None:
        res = _evaluar_cierre_coherente(999, [])
        self.assertIs(res.resultado, Resultado.NO_EVALUABLE)
        self.assertEqual(res.evidencia, ())

    def test_to_dict_serializa_resultado_y_evidencia(self) -> None:
        eventos = [(_t(0), "open"), (_t(60), "solved"), (_t(120), "closed")]
        res = _evaluar_cierre_coherente(321, eventos)
        d = res.to_dict()
        self.assertEqual(d["ticket_id"], 321)
        self.assertEqual(d["resultado"], "thumbs_up")
        self.assertEqual(d["evidencia"][0]["valor"], "closed")
        self.assertIsNotNone(d["evidencia"][0]["timestamp"])


class PendingPostSolvedTests(unittest.TestCase):
    def test_sin_solved_es_thumbs_up(self) -> None:
        eventos = [(_t(0), "new"), (_t(1), "open"), (_t(10), "pending")]
        res = _evaluar_pending_post_solved(1, eventos, interacciones_in=[])
        self.assertIs(res.resultado, Resultado.THUMBS_UP)

    def test_solved_y_pending_sin_trigger_es_thumbs_down(self) -> None:
        eventos = [
            (_t(0), "open"),
            (_t(60), "solved"),
            (_t(120), "pending"),  # sin interaccion in entre _t(60) y _t(120)
        ]
        res = _evaluar_pending_post_solved(2, eventos, interacciones_in=[])
        self.assertIs(res.resultado, Resultado.THUMBS_DOWN)
        self.assertEqual(len(res.evidencia), 1)

    def test_solved_y_pending_con_trigger_del_merchant_es_thumbs_up(self) -> None:
        eventos = [
            (_t(0), "open"),
            (_t(60), "solved"),
            (_t(120), "pending"),
        ]
        # merchant respondió entre solved y pending
        res = _evaluar_pending_post_solved(3, eventos, interacciones_in=[_t(90)])
        self.assertIs(res.resultado, Resultado.THUMBS_UP)

    def test_dos_ciclos_uno_malo_uno_bueno(self) -> None:
        eventos = [
            (_t(0), "open"),
            (_t(60), "solved"),
            (_t(70), "pending"),     # malo: sin trigger
            (_t(120), "open"),
            (_t(180), "solved"),
            (_t(240), "pending"),    # bueno: hay interaccion in en t=210
        ]
        res = _evaluar_pending_post_solved(4, eventos, interacciones_in=[_t(210)])
        self.assertIs(res.resultado, Resultado.THUMBS_DOWN)
        self.assertEqual(len(res.evidencia), 1)

    def test_pending_antes_de_solved_no_cuenta(self) -> None:
        eventos = [
            (_t(0), "open"),
            (_t(10), "pending"),  # primer pending: no hay solved previo, ignorado
            (_t(60), "solved"),
            (_t(1440), "closed"),
        ]
        res = _evaluar_pending_post_solved(5, eventos, interacciones_in=[])
        self.assertIs(res.resultado, Resultado.THUMBS_UP)

    def test_confianza_es_parcial(self) -> None:
        eventos = [(_t(0), "open"), (_t(60), "solved"), (_t(120), "pending")]
        res = _evaluar_pending_post_solved(6, eventos, interacciones_in=[])
        self.assertIs(res.confianza, Confianza.PARCIAL)


class HoldSinSideConvTests(unittest.TestCase):
    def test_detectar_tramos_hold_cerrado(self) -> None:
        eventos = [
            (_t(0), "open"),
            (_t(60), "hold"),
            (_t(120), "open"),
        ]
        tramos = _detectar_tramos_hold(eventos)
        self.assertEqual(len(tramos), 1)
        ini, fin, h = tramos[0]
        self.assertEqual(ini, _t(60))
        self.assertEqual(fin, _t(120))
        self.assertAlmostEqual(h, 1.0, places=2)

    def test_detectar_tramos_multiples(self) -> None:
        eventos = [
            (_t(0), "open"),
            (_t(60), "hold"),
            (_t(120), "open"),
            (_t(180), "hold"),
            (_t(240), "solved"),
        ]
        tramos = _detectar_tramos_hold(eventos)
        self.assertEqual(len(tramos), 2)

    def test_detectar_tramo_abierto(self) -> None:
        eventos = [(_t(0), "open"), (_t(60), "hold")]
        tramos = _detectar_tramos_hold(eventos)
        self.assertEqual(len(tramos), 1)
        self.assertIsNone(tramos[0][1])
        self.assertIsNone(tramos[0][2])

    def test_sin_eventos_no_evaluable(self) -> None:
        res = _evaluar_hold_sin_side_conv(1, [], set(), None)
        self.assertIs(res.resultado, Resultado.NO_EVALUABLE)

    def test_error_side_conv_no_evaluable(self) -> None:
        res = _evaluar_hold_sin_side_conv(2, [(_t(0), "open")], set(), "table not found")
        self.assertIs(res.resultado, Resultado.NO_EVALUABLE)
        self.assertIn("table not found", res.nota or "")

    def test_sin_hold_thumbs_up(self) -> None:
        eventos = [(_t(0), "open"), (_t(60), "solved"), (_t(120), "closed")]
        res = _evaluar_hold_sin_side_conv(3, eventos, set(), None)
        self.assertIs(res.resultado, Resultado.THUMBS_UP)
        self.assertIn("no estuvo en hold", res.nota or "")

    def test_hold_corto_thumbs_up(self) -> None:
        # 1h en hold << 24h umbral
        eventos = [(_t(0), "open"), (_t(60), "hold"), (_t(120), "open")]
        res = _evaluar_hold_sin_side_conv(4, eventos, set(), None)
        self.assertIs(res.resultado, Resultado.THUMBS_UP)

    def test_hold_largo_sin_side_conv_thumbs_down(self) -> None:
        # 25h en hold sin side conv → thumbs_down
        eventos = [(_t(0), "open"), (_t(60), "hold"), (_t(60 + 25 * 60), "open")]
        res = _evaluar_hold_sin_side_conv(5, eventos, set(), None)
        self.assertIs(res.resultado, Resultado.THUMBS_DOWN)
        self.assertEqual(len(res.evidencia), 1)

    def test_hold_largo_con_side_conv_thumbs_up(self) -> None:
        eventos = [(_t(0), "open"), (_t(60), "hold"), (_t(60 + 25 * 60), "open")]
        res = _evaluar_hold_sin_side_conv(6, eventos, {6}, None)
        self.assertIs(res.resultado, Resultado.THUMBS_UP)
        self.assertIn("side conversation", res.nota or "")


if __name__ == "__main__":
    unittest.main()
