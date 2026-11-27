"""The two RAG metrics that need no model."""

import pytest
from evals.metrics import citation_validity, context_recall

Q3 = "q3-business-review.pdf"
HB = "employee-handbook.docx"


def test_recall_is_the_share_of_reference_chunks_that_were_retrieved() -> None:
    assert context_recall([(Q3, 2)], [(Q3, 2), (HB, None)]) == 1.0
    assert context_recall([(Q3, 1), (Q3, 3)], [(Q3, 3)]) == 0.5
    assert context_recall([(Q3, 2)], []) == 0.0  # the model never searched


def test_a_page_must_match_for_a_pdf_but_a_file_is_enough_without_pages() -> None:
    assert context_recall([(Q3, 2)], [(Q3, 1)]) == 0.0
    assert context_recall([(HB, None)], [(HB, None)]) == 1.0


def test_no_references_has_no_recall() -> None:
    with pytest.raises(ValueError, match="reference"):
        context_recall([], [(Q3, 1)])


def test_citation_validity_counts_markers_that_point_at_a_returned_passage() -> None:
    assert citation_validity("Churn fell [1] because of onboarding [2].", [1, 2]) == 1.0
    assert citation_validity("Churn fell [1] and rose [7].", [1, 2]) == 0.5


def test_an_answer_without_any_marker_scores_zero() -> None:
    assert citation_validity("Churn fell.", [1, 2]) == 0.0
    assert citation_validity("Churn fell [1].", []) == 0.0  # nothing was returned to cite


def test_repeated_markers_count_each_time_and_other_brackets_are_ignored() -> None:
    assert citation_validity("A [1]. B [1]. See [note] and [x1].", [1]) == 1.0
