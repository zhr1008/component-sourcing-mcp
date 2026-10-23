"""Reusable input types exported as JSON Schema by FastMCP/Pydantic."""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints

Identifier = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
    Field(
        description="Exact domestic LCSC C-code (e.g. C2040) or exact manufacturer part number; never a fuzzy description"
    ),
]
Query = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
    Field(
        description="Model or functional keywords, e.g. RS485 transceiver; combine with package and electrical constraints"
    ),
]
ResultLimit = Annotated[
    int, Field(ge=1, le=50, description="Maximum returned candidates, 1–50")
]
Money = Annotated[
    float,
    Field(
        ge=0,
        allow_inf_nan=False,
        description="Domestic CNY unit price at the smallest order tier",
    ),
]
Page = Annotated[
    int, Field(ge=1, le=1000, description="One-based upstream page number")
]
Quantity = Annotated[
    int,
    Field(
        ge=1,
        le=1_000_000_000,
        description="Requested purchase quantity; MOQ and order multiples are reported separately",
    ),
]
Identifiers = Annotated[list[Identifier], Field(min_length=1, max_length=20)]
LibraryType = Literal["all", "basic", "preferred", "extended", "no_fee"]
ConstraintText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
Constraints = Annotated[
    dict[ConstraintText, ConstraintText],
    Field(
        min_length=1,
        max_length=30,
        description='Electrical constraints with units, e.g. {"output_voltage":"=3.3V","accuracy":"<=0.2%"}; inspect all_matched',
    ),
]
