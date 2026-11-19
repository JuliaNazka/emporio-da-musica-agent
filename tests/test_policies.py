import pytest

from emporio.policies import PolicyIndex, search_policies


@pytest.fixture(scope="module")
def index():
    return PolicyIndex.from_pdf()


def top_sections(index, query):
    return [r["section"] for r in index.search(query)]


def test_manual_is_split_into_numbered_sections(index):
    sections = [s.section for s in index.sections]
    assert len(sections) == len(set(sections))
    assert {"1.2", "2", "3.1", "4.1", "4.2", "5.2", "6.2", "7.3", "8.1", "9", "10"} <= set(sections)


def test_page_headers_and_footers_are_removed(index):
    assert all("Página" not in s.text for s in index.sections)


def test_subsections_keep_their_topic(index):
    section = next(s for s in index.sections if s.section == "4.2")
    assert section.topic == "Política de Trocas e Devoluções"


@pytest.mark.parametrize(
    "query, expected",
    [
        ("posso devolver o violão que comprei?", "4.1"),
        ("dá pra parcelar em 10x?", "3.1"),
        ("vocês vendem cordas de guitarra?", "1"),
        ("que horas abre no sábado?", "2"),
        ("meu instrumento veio com defeito", "4.2"),
        ("vocês guardam meus dados pessoais?", "9"),
        ("qual o endereço da loja?", "1.2"),
        ("pix tem desconto em produto na promoção?", "6.2"),
        ("rastreamento do pedido", "5.3"),
        ("quero fazer uma reclamação", "7.3"),
    ],
)
def test_question_finds_the_right_section_first(index, query, expected):
    assert top_sections(index, query)[0] == expected


def test_shipping_question_reaches_shipping_sections(index):
    assert {"5.1", "5.2"} & set(top_sections(index, "quanto custa o frete pra Dourados?"))


def test_unrelated_question_finds_nothing(index):
    assert not search_policies(index, "xyzzy")["found"]
