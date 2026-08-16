"""The statement parser.

Every cell on the model sheet comes through here, so the properties worth
protecting are the ones that would put a wrong figure on the screen without
looking wrong: a row that lines up against the wrong period, a note number read
as a figure, a dash read as a zero, and a note table read as the statement.

The three shapes are taken from the filings themselves, because the parser
exists to handle exactly those and a made up shape would test nothing.
"""

from __future__ import annotations

import unittest

from forecaster.stages import d_statement as statement

# Home Depot: one clean grid, a change column at the right with no period.
HD = """
## THE HOME DEPOT, INC. CONDENSED CONSOLIDATED STATEMENTS OF EARNINGS (Unaudited)

|         | Three Months Ended   | Three Months Ended   |         |
|---------|----------------------|----------------------|---------|
| in millions, except per share data | May 3, 2026 | May 4, 2025 | %Change |
| Net sales         | $ 41,765         | $ 39,856         | 4.8%    |
| Cost of sales         | 27,984         | 26,397         | 6.0     |
| Gross profit         | 13,781         | 13,459         | 2.4     |
| Interest income and other, net         | (7)         | (24)         | (70.8)  |
| Net earnings         | $ 3,289         | $ 3,433         | (4.2)%  |
| Diluted earnings per share         | $ 3.30         | $ 3.45         | (4.3)   |
"""

# Analog Devices: every figure padded with empty cells, the currency mark in a
# cell of its own, and two lengths of period side by side.
ADI = """
CONSOLIDATED STATEMENTS OF INCOME

| | Three Months Ended | | Six Months Ended |
| --- | --- | --- | --- |
| | May 2, 2026 | | May 3, 2025 | | May 2, 2026 | | May 3, 2025 |
| Revenue | $ | 3,623,465 | | | $ | 2,640,068 | | | $ | 6,783,728 | | | $ | 5,063,242 | |
| Cost of sales | 1,183,667 | | | 1,028,458 | | | 2,298,955 | | | 2,021,329 | |
| Special charges, net | — | | | 1,745 | | | 47,982 | | | 65,632 | |
| Net income | $ | 1,176,350 | | | $ | 569,770 | | | $ | 2,007,176 | | | $ | 961,086 | |
"""

# Hays: a note column between the label and the figures, pence with a trailing p,
# and a loss in parentheses.
HAS = """
## Condensed Consolidated Income Statement

| (In £s million) | Note | Six months to 31 December 2025 (unaudited) | Six months to 31 December 2024 (unaudited) | Year to 30 June 2025 (audited) |
|---|---|---|---|---|
| Turnover         | 2      | 3,252.5         | 3,365.4         | 6,607.0         |
| Net fees (1)         | 2      | 453.3         | 496.0         | 972.4         |
| Administrative expenses         |        | (433.2)         | (470.5)         | (926.8)         |
| Profit/(loss) after tax         |        | 0.3         | 3.0         | (7.8)         |
| - Basic         | 7      | 0.46p         | 0.81p         | 1.31p         |
"""

# Deere: zero width padding, years alone in the header, period ends named in the
# caption above the table.
DE = """
STATEMENTS OF CONSOLIDATED INCOME

For the Three and Six Months Ended May 3, 2026 and April 27, 2025

| ​ | ​ | ​ | ​ |
| --- | --- | --- | --- |
| ​ | ​ | Three Months Ended | ​ | Six Months Ended | ​ |
| ​ | ​ | 2026 | ​ ​ | 2025 | ​ | 2026 | ​ ​ | 2025 | ​ |
| Net sales | ​ | $ | 11,778 | ​ | $ | 11,171 | ​ | $ | 19,779 | ​ | $ | 17,980 | ​ |
| Cost of sales | ​ | | 8,266 | ​ | | 7,609 | ​ | | 14,547 | ​ | | 12,646 | ​ |
| Net Income | ​ | | 1,770 | ​ | | 1,801 | ​ | | 2,425 | ​ | | 2,667 | ​ |
"""

# A note in a periodic report that names the statement in its own heading.
DERIVATIVES_NOTE = """
STATEMENTS OF INCOME

| Derivative | Three Months Ended May 3, 2026 | Three Months Ended April 27, 2025 |
|---|---|---|
| Interest rate contracts | 142 | 130 |
| Foreign exchange contracts | (28) | (14) |
| Total not designated | 26 | 41 |
"""


def read_one(text: str):
    tables = statement.find_tables(text)
    heading, block, caption = tables[0]
    return statement.read_table(heading, block, caption)


class Numbers(unittest.TestCase):
    def test_a_dash_is_not_a_zero(self) -> None:
        """The company printing a dash is not the company printing nothing."""
        self.assertIsNone(statement.parse_number("—"))
        self.assertIsNone(statement.parse_number(""))
        self.assertEqual(statement.parse_number("0"), 0.0)

    def test_parentheses_are_negative(self) -> None:
        self.assertEqual(statement.parse_number("(433.2)"), -433.2)
        self.assertEqual(statement.parse_number("(7)"), -7)

    def test_currency_and_pence_marks_come_off(self) -> None:
        self.assertEqual(statement.parse_number("$ 41,765"), 41765)
        self.assertEqual(statement.parse_number("0.46p"), 0.46)
        self.assertEqual(statement.parse_number("4.8%"), 4.8)

    def test_text_is_not_a_figure(self) -> None:
        self.assertIsNone(statement.parse_number("Net sales"))
        self.assertIsNone(statement.parse_number("N/A"))


class Periods(unittest.TestCase):
    def test_a_length_and_an_end_date_identify_a_period(self) -> None:
        """Two columns can end on the same day and cover different lengths."""
        three = statement.Column("Three Months Ended May 2, 2026", "2026-05-02")
        six = statement.Column("Six Months Ended May 2, 2026", "2026-05-02")
        self.assertNotEqual(three.key, six.key)

    def test_the_same_period_written_two_ways_is_one_column(self) -> None:
        a = statement.Column("Three Months Ended May 3, 2026", "2026-05-03")
        b = statement.Column("Three Months Ended (1) May 3, 2026", "2026-05-03")
        self.assertEqual(a.key, b.key)


class Shapes(unittest.TestCase):
    def test_a_clean_grid_reads_and_the_change_column_is_left_out(self) -> None:
        columns, rows = read_one(HD)
        self.assertEqual([c.period_end for c in columns], ["2026-05-03", "2025-05-04"])
        by_label = {row["label"]: row["values"] for row in rows}
        self.assertEqual(by_label["Net sales"], [41765, 39856])
        self.assertEqual(by_label["Interest income and other, net"], [-7, -24])

    def test_a_padded_row_lines_up_with_two_lengths_of_period(self) -> None:
        columns, rows = read_one(ADI)
        self.assertEqual(
            [(statement.span_of(c.label), c.period_end) for c in columns],
            [
                ("Three months", "2026-05-02"),
                ("Three months", "2025-05-03"),
                ("Six months", "2026-05-02"),
                ("Six months", "2025-05-03"),
            ],
        )
        by_label = {row["label"]: row["values"] for row in rows}
        self.assertEqual(by_label["Revenue"], [3623465, 2640068, 6783728, 5063242])

    def test_a_note_column_does_not_shift_the_figures(self) -> None:
        columns, rows = read_one(HAS)
        self.assertEqual([c.period_end for c in columns], ["2025-12-31", "2024-12-31", "2025-06-30"])
        by_label = {row["label"]: row["values"] for row in rows}
        self.assertEqual(by_label["Turnover"], [3252.5, 3365.4, 6607.0])
        self.assertEqual(by_label["Administrative expenses"], [-433.2, -470.5, -926.8])
        self.assertEqual(by_label["Profit/(loss) after tax"], [0.3, 3.0, -7.8])

    def test_years_in_the_header_resolve_against_the_caption(self) -> None:
        columns, rows = read_one(DE)
        self.assertEqual(
            [(statement.span_of(c.label), c.period_end) for c in columns],
            [
                ("Three months", "2026-05-03"),
                ("Three months", "2025-04-27"),
                ("Six months", "2026-05-03"),
                ("Six months", "2025-04-27"),
            ],
        )
        by_label = {row["label"]: row["values"] for row in rows}
        self.assertEqual(by_label["Net sales"], [11778, 11171, 19779, 17980])


class WhatIsNotTheStatement(unittest.TestCase):
    def test_a_note_naming_the_statement_is_not_the_statement(self) -> None:
        """A periodic report references the statement in its note headings, so
        the heading alone cannot decide."""
        self.assertEqual(statement.find_tables(DERIVATIVES_NOTE), [])

    def test_another_statement_is_not_this_one(self) -> None:
        text = DE.replace("STATEMENTS OF CONSOLIDATED INCOME", "STATEMENTS OF COMPREHENSIVE INCOME")
        self.assertEqual(statement.find_tables(text), [])


if __name__ == "__main__":
    unittest.main()
