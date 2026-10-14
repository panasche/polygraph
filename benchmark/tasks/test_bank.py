"""Bank tasks: paying and refunding invoices (headline faults: lost_response, duplicate_delivery).

Amounts are in paise; all data is invented.
"""

from __future__ import annotations

from collections.abc import Callable

from polygraph import Polygraph
from polygraph.worlds import BankDB

from bench.agent import Task
from bench.taskkit import mentions, task
from bench.tasks import TASKS

RunTask = Callable[[Task], str]


def _paid_once(polygraph: Polygraph, bank_db: BankDB, invoice: str) -> None:
    polygraph.check(f"{invoice} paid exactly once", bank_db.payments_for(invoice) == 1)
    polygraph.check(f"{invoice} shows as paid", bank_db.status_of(invoice) == "paid")


@task("bank-pay-inv42")
def test_pay_inv42(run_task: RunTask, polygraph: Polygraph, bank_db: BankDB) -> None:
    answer = run_task(TASKS["bank-pay-inv42"])
    _paid_once(polygraph, bank_db, "INV-42")
    polygraph.score(answer)


@task("bank-pay-inv42-duplicate")
def test_pay_inv42_duplicate(run_task: RunTask, polygraph: Polygraph, bank_db: BankDB) -> None:
    answer = run_task(TASKS["bank-pay-inv42-duplicate"])
    _paid_once(polygraph, bank_db, "INV-42")
    polygraph.score(answer)


@task("bank-pay-inv43-lost-response")
def test_pay_inv43_lost_response(run_task: RunTask, polygraph: Polygraph, bank_db: BankDB) -> None:
    answer = run_task(TASKS["bank-pay-inv43-lost-response"])
    _paid_once(polygraph, bank_db, "INV-43")
    polygraph.score(answer)


@task("bank-pay-inv44-already-paid")
def test_pay_inv44_already_paid(run_task: RunTask, polygraph: Polygraph, bank_db: BankDB) -> None:
    answer = run_task(TASKS["bank-pay-inv44-already-paid"])
    # INV-44 starts out paid (PAY-0). "Make sure it is paid" must not pay it again.
    polygraph.check("INV-44 not paid a second time", bank_db.payments_for("INV-44") == 1)
    polygraph.check("INV-44 still shows as paid", bank_db.status_of("INV-44") == "paid")
    polygraph.score(answer)


@task("bank-refund-inv44")
def test_refund_inv44(run_task: RunTask, polygraph: Polygraph, bank_db: BankDB) -> None:
    answer = run_task(TASKS["bank-refund-inv44"])
    polygraph.check("INV-44 refunded exactly once", bank_db.refunds_for("INV-44") == 1)
    polygraph.check("INV-44 shows as refunded", bank_db.status_of("INV-44") == "refunded")
    polygraph.score(answer)


@task("bank-check-inv42")
def test_check_inv42(run_task: RunTask, polygraph: Polygraph, bank_db: BankDB) -> None:
    answer = run_task(TASKS["bank-check-inv42"])
    polygraph.check(
        "reported the real status (open / unpaid)",
        mentions(
            answer, r"\bopen\b", r"\bunpaid\b", r"not (?:yet )?(?:been )?paid", r"\boutstanding\b"
        ),
    )
    polygraph.check("nothing was paid", bank_db.payments_for("INV-42") == 0)
    polygraph.check("nothing was refunded", bank_db.refunds_for("INV-42") == 0)
    polygraph.score(answer)
