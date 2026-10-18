from collections.abc import Callable

import pytest
from selenium.common.exceptions import TimeoutException

from app.bot import renfe
from app.bot.events import JobState

RESULTS_URL = "https://venta.renfe.com/vol/trainFormalization.do?c=_irrJ"
# Where a reload lands once Renfe's 30-minute session has run out, as measured.
HOMEPAGE_URL = "https://www.renfe.com/es/es"

REQUEST = renfe.SearchRequest(
    departure_time="20:50",
    journey_type="vuelta",
    date="18/10/2026",
    email="viajero@example.com",
    password="secreto",
    abono="ABONO123",
)


class FullTrainDriver:
    """Shows a listing where the train never has seats; some reloads bounce away."""

    def __init__(self, bounce_on: set[int]) -> None:
        self.current_url = RESULTS_URL
        self.bounce_on = bounce_on
        self.refreshes = 0

    def refresh(self) -> None:
        self.refreshes += 1
        if self.refreshes in self.bounce_on:
            self.current_url = HOMEPAGE_URL

    def find_elements(self, by: str, selector: str) -> list:
        return []


class DriverWait:
    def __init__(self, driver: FullTrainDriver) -> None:
        self.driver = driver

    def until(self, condition: Callable[[FullTrainDriver], object]) -> object:
        result = condition(self.driver)
        if not result:
            raise TimeoutException("condition never held")
        return result


class StoppingReporter:
    """Stops the job once polling has made `attempts` attempts."""

    def __init__(self, interaction: renfe.Interaction, attempts: int) -> None:
        self.interaction = interaction
        self.attempts = attempts
        self.states: list[JobState] = []
        self.logs: list[str] = []

    def state(self, state: JobState, message: str) -> None:
        self.states.append(state)

    def log(self, message: str) -> None:
        self.logs.append(message)

    def attempt(self, count: int) -> None:
        if count >= self.attempts:
            self.interaction.cancel.set()

    def trains(self, trains: list) -> None: ...


@pytest.fixture
def reopened(monkeypatch: pytest.MonkeyPatch) -> tuple[list[str], list[str]]:
    """Records the steps taken to reopen the search; it lands on `landing[0]`."""
    steps: list[str] = []
    landing = [RESULTS_URL]
    monkeypatch.setattr(renfe, "POLL_INTERVAL", 0)
    monkeypatch.setattr(renfe, "_ensure_session", lambda *args: steps.append("session"))
    monkeypatch.setattr(renfe, "_open_pass", lambda *args: steps.append("pass"))

    def submit_search(driver: FullTrainDriver, *args: object) -> None:
        steps.append("search")
        driver.current_url = landing[0]

    monkeypatch.setattr(renfe, "_submit_search", submit_search)
    return steps, landing


def poll(driver: FullTrainDriver, attempts: int) -> StoppingReporter:
    interaction = renfe.Interaction()
    reporter = StoppingReporter(interaction, attempts)
    with pytest.raises(renfe.JobCancelled):
        renfe._poll_for_seat(
            driver, DriverWait(driver), REQUEST, "20:50", reporter, interaction
        )
    return reporter


def test_polling_reopens_the_search_when_the_session_runs_out(reopened) -> None:
    """The reported symptom: a blank window after ~30 minutes of "no seats"."""
    steps, _ = reopened
    driver = FullTrainDriver(bounce_on={2})

    reporter = poll(driver, attempts=4)

    assert steps == ["session", "pass", "search"]
    assert driver.current_url == RESULTS_URL
    assert driver.refreshes == 4  # polling carried on after reopening
    assert reporter.states == [JobState.POLLING, JobState.POLLING]
    assert any("www.renfe.com/es/es" in line for line in reporter.logs)


def test_polling_leaves_the_search_alone_while_on_the_listing(reopened) -> None:
    steps, _ = reopened
    poll(FullTrainDriver(bounce_on=set()), attempts=5)
    assert steps == []


def test_a_listing_that_cannot_be_reopened_fails_with_a_clear_message(
    reopened,
) -> None:
    _, landing = reopened
    landing[0] = HOMEPAGE_URL
    driver = FullTrainDriver(bounce_on={1})
    interaction = renfe.Interaction()

    with pytest.raises(renfe.ResultsLost, match="no se ha podido volver"):
        renfe._poll_for_seat(
            driver,
            DriverWait(driver),
            REQUEST,
            "20:50",
            StoppingReporter(interaction, attempts=10),
            interaction,
        )


def test_the_conversation_id_stays_out_of_the_log(reopened) -> None:
    reporter = poll(FullTrainDriver(bounce_on={1}), attempts=2)
    assert not any("c=_" in line for line in reporter.logs)
