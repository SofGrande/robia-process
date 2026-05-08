"""Tests del catálogo de tópicos/naturalezas — pura lógica, sin Sheet."""
from __future__ import annotations

import unittest

from robia_procesos.core import topicos_catalogo as cat


class NormalizarTests(unittest.TestCase):
    def test_quita_tildes_y_pasa_a_lower(self) -> None:
        self.assertEqual(cat.normalizar("Configuración"), "configuracion")

    def test_quita_separadores_y_espacios(self) -> None:
        self.assertEqual(cat.normalizar("Pago Nube / Cuenta-2"), "pagonubecuenta2")

    def test_string_vacio_o_none(self) -> None:
        self.assertEqual(cat.normalizar(""), "")
        self.assertEqual(cat.normalizar(None), "")
        self.assertEqual(cat.normalizar("   "), "")

    def test_normaliza_iguales_distintos_idiomas(self) -> None:
        # 'Dúvida Autoatendimento' (PT) y 'Duda Autoatención' (ES) NO normalizan
        # iguales — para eso están los aliases. Acá solo validamos la
        # forma normalizada propia.
        self.assertEqual(cat.normalizar("Dúvida Autoatendimento"), "duvidaautoatendimento")
        self.assertEqual(cat.normalizar("Duda Autoatención"), "dudaautoatencion")


class CatalogoTests(unittest.TestCase):
    def setUp(self) -> None:
        # construimos un catálogo en memoria sin tocar el Sheet
        self.catalogo = cat._construir_desde_filas(
            combinaciones=[
                ("AR", "Online", "Configuraciones Online", "Idiomas Y Monedas"),
                ("AR", "Pago Nube", "Cuenta", "Cuotas - Pago Nube"),
                ("BR", "Configurações Essenciais", "Account", "2FA"),
            ],
            naturalezas=[
                ("LATAM", "Duda Autoatención"),
                ("LATAM", "Duda Investigativa"),
                ("LATAM", "Issue"),
                ("LATAM", "Problem"),
                ("BR", "Dúvida Autoatendimento"),
            ],
        )

    def test_combinacion_valida_match_exacto(self) -> None:
        self.assertTrue(
            self.catalogo.combinacion_valida(
                "Online", "Configuraciones Online", "Idiomas Y Monedas"
            )
        )

    def test_combinacion_valida_tolera_tildes_y_caps(self) -> None:
        self.assertTrue(
            self.catalogo.combinacion_valida(
                "online", "configuraciones  online", "Idíomás y Monedas".replace("í", "i")
            )
        )

    def test_combinacion_invalida(self) -> None:
        self.assertFalse(
            self.catalogo.combinacion_valida("Online", "Otro", "Idiomas Y Monedas")
        )

    def test_subtopico_existe_en_otra_geo(self) -> None:
        # "2FA" existe en BR, debe encontrarse aunque preguntemos sin geo
        self.assertTrue(self.catalogo.subtopico_existe("2FA"))
        self.assertFalse(self.catalogo.subtopico_existe("inexistente"))

    def test_naturaleza_valida_directa(self) -> None:
        self.assertTrue(self.catalogo.naturaleza_valida("Issue"))
        self.assertTrue(self.catalogo.naturaleza_valida("Duda Autoatención"))

    def test_naturaleza_valida_via_alias_lake_hibrido(self) -> None:
        # el lake muestra 'Duda/Dúvida Auto' — debe matchear vía alias
        self.assertTrue(self.catalogo.naturaleza_valida("Duda/Dúvida Auto"))
        self.assertTrue(self.catalogo.naturaleza_valida("Duda/Dúvida Investigativa"))
        self.assertTrue(self.catalogo.naturaleza_valida("Problem/Feedback"))

    def test_naturaleza_invalida(self) -> None:
        self.assertFalse(self.catalogo.naturaleza_valida(""))
        self.assertFalse(self.catalogo.naturaleza_valida("Naturaleza Inventada"))


if __name__ == "__main__":
    unittest.main()
