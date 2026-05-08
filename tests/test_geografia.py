"""Tests del parser de guru_name a Geografia. No tocan lake."""
from __future__ import annotations

import unittest

from robia_procesos.core.geografia import Geografia, parsear_guru_name


class ParserGuruNameTests(unittest.TestCase):
    def test_tiendanube_es_latam(self) -> None:
        self.assertIs(parsear_guru_name("Dagmara de Tiendanube"), Geografia.LATAM)
        self.assertIs(parsear_guru_name("Alejandra S. de Tiendanube"), Geografia.LATAM)

    def test_nuvemshop_es_br(self) -> None:
        self.assertIs(parsear_guru_name("Adauto da Nuvemshop"), Geografia.BR)
        self.assertIs(parsear_guru_name("Alan L. da Nuvemshop"), Geografia.BR)

    def test_agentes_virtuales(self) -> None:
        self.assertIs(parsear_guru_name("Agente Virtual AR"), Geografia.LATAM)
        self.assertIs(parsear_guru_name("Agente Virtual BR - Claudia"), Geografia.BR)

    def test_legacy_sin_patron(self) -> None:
        self.assertIs(parsear_guru_name("Adrian"), Geografia.DESCONOCIDA)
        self.assertIs(parsear_guru_name("Ale"), Geografia.DESCONOCIDA)
        self.assertIs(parsear_guru_name(""), Geografia.DESCONOCIDA)
        self.assertIs(parsear_guru_name(None), Geografia.DESCONOCIDA)

    def test_case_insensitive(self) -> None:
        self.assertIs(parsear_guru_name("ALAN T. DA NUVEMSHOP"), Geografia.BR)


if __name__ == "__main__":
    unittest.main()
