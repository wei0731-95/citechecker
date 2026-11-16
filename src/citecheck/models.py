from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class CitationType(str, Enum):
    STATUTE = "STATUTE"
    JUDGMENT = "JUDGMENT"


class StatuteStatus(str, Enum):
    FOUND = "FOUND"
    LAW_NOT_FOUND = "LAW_NOT_FOUND"
    ARTICLE_NOT_FOUND = "ARTICLE_NOT_FOUND"
    ARTICLE_DELETED = "ARTICLE_DELETED"
    UNRESOLVED_REF = "UNRESOLVED_REF"


class JudgmentStatus(str, Enum):
    FOUND = "FOUND"
    NOT_FOUND_IN_SCOPE = "NOT_FOUND_IN_SCOPE"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    AMBIGUOUS = "AMBIGUOUS"
    DATA_NOT_COVERED = "DATA_NOT_COVERED"


class SegmentRole(str, Enum):
    MAIN_TEXT = "MAIN_TEXT"
    PARTY_CLAIM = "PARTY_CLAIM"
    LOWER_COURT = "LOWER_COURT"
    SUPREME_COURT = "SUPREME_COURT"
    UNKNOWN = "UNKNOWN"


class Relation(str, Enum):
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    UNRELATED = "UNRELATED"


class L2Label(str, Enum):
    COURT_HOLDING = "COURT_HOLDING"
    PARTY_ARGUMENT = "PARTY_ARGUMENT"
    LOWER_COURT_VIEW = "LOWER_COURT_VIEW"
    CONTRADICTED = "CONTRADICTED"
    NOT_IN_JUDGMENT = "NOT_IN_JUDGMENT"
    UNCERTAIN = "UNCERTAIN"
    NOT_RUN = "NOT_RUN"


class Span(BaseModel):
    start: int  # offset in the NORMALIZED brief text, inclusive
    end: int    # exclusive


class StatuteCitation(BaseModel):
    type: Literal["STATUTE"] = "STATUTE"
    raw: str
    span: Span
    law_name: str | None
    article: str | None
    paragraph: int | None = None
    subparagraph: int | None = None
    # Placeholder until verify.py runs (extract.py only sets UNRESOLVED_REF itself).
    status: StatuteStatus = StatuteStatus.LAW_NOT_FOUND
    article_text: str | None = None


class L2Evidence(BaseModel):
    segment_index: int
    segment_role: SegmentRole
    passage: str  # at most 400 chars
    relation: Relation
    confidence: float


class JudgmentCitation(BaseModel):
    type: Literal["JUDGMENT"] = "JUDGMENT"
    raw: str
    span: Span
    court: str | None
    year: int | None
    case_word: str | None
    case_no: int | None
    doc_kind: str | None
    # Placeholder until verify.py runs (extract.py sets OUT_OF_SCOPE itself).
    status: JudgmentStatus = JudgmentStatus.NOT_FOUND_IN_SCOPE
    jid: str | None = None
    claim_sentence: str | None = None
    l2_label: L2Label = L2Label.NOT_RUN
    l2_evidence: L2Evidence | None = None


class DataCoverage(BaseModel):
    laws_update_date: str | None = None
    judgment_year_min: int | None = None
    judgment_year_max: int | None = None
    judgment_date_min: str | None = None
    judgment_date_max: str | None = None
    judgment_count: int = 0


class Report(BaseModel):
    input_file: str
    generated_at: str
    data_coverage: DataCoverage
    normalized_text: str = Field(description="Normalized brief text; all spans index into it")
    statutes: list[StatuteCitation]
    judgments: list[JudgmentCitation]
    warnings: list[str] = Field(default_factory=list)
