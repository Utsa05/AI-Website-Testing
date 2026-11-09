document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("run-form");
  const runBtn = document.getElementById("run-btn");
  const formError = document.getElementById("form-error");
  const statusCard = document.getElementById("status-card");
  const stepsList = document.getElementById("steps-list");
  const statusMessage = document.getElementById("status-message");
  const statusError = document.getElementById("status-error");
  const reportLink = document.getElementById("report-link");

  let pollTimer = null;

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    formError.textContent = "";
    reportLink.style.display = "none";
    statusError.style.display = "none";

    const payload = {
      api_key: document.getElementById("api_key").value,
      model: document.getElementById("model").value,
      target_url: document.getElementById("target_url").value,
      headless: document.getElementById("headless").checked,
    };

    runBtn.disabled = true;
    runBtn.textContent = "Starting...";

    try {
      const res = await fetch("/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();

      if (!res.ok) {
        formError.textContent = data.error || "Failed to start run.";
        runBtn.disabled = false;
        runBtn.textContent = "RUN AI TEST";
        return;
      }

      statusCard.style.display = "block";
      resetSteps();
      pollStatus(data.run_id);
    } catch (err) {
      formError.textContent = "Network error starting the run: " + err;
      runBtn.disabled = false;
      runBtn.textContent = "RUN AI TEST";
    }
  });

  function resetSteps() {
    [...stepsList.children].forEach((li) => {
      li.classList.remove("active", "complete");
    });
  }

  function updateSteps(stepIndex, done) {
    [...stepsList.children].forEach((li) => {
      const idx = parseInt(li.dataset.step, 10);
      li.classList.remove("active", "complete");
      if (idx < stepIndex || (done && idx <= stepIndex)) {
        li.classList.add("complete");
      } else if (idx === stepIndex && !done) {
        li.classList.add("active");
      }
    });
  }

  function pollStatus(runId) {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(async () => {
      try {
        const res = await fetch(`/status/${runId}`);
        const data = await res.json();
        if (!res.ok) {
          statusError.style.display = "block";
          statusError.textContent = data.error || "Unknown error fetching status.";
          clearInterval(pollTimer);
          runBtn.disabled = false;
          runBtn.textContent = "RUN AI TEST";
          return;
        }

        const st = data.status;
        updateSteps(st.step_index, st.done);
        statusMessage.textContent = st.message || st.step_name;

        if (st.error) {
          statusError.style.display = "block";
          statusError.textContent = st.error;
        }

        if (st.done) {
          clearInterval(pollTimer);
          runBtn.disabled = false;
          runBtn.textContent = "RUN AI TEST";
          if (!st.error) {
            reportLink.style.display = "inline-block";
            reportLink.href = `/report/${runId}/view`;
            window.location.href = `/report/${runId}/view`;
          }
        }
      } catch (err) {
        statusError.style.display = "block";
        statusError.textContent = "Network error polling status: " + err;
        clearInterval(pollTimer);
        runBtn.disabled = false;
        runBtn.textContent = "RUN AI TEST";
      }
    }, 1200);
  }
});
