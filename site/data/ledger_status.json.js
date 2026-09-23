// Build-time summary of the prospective ledger, read from the attempt records
// the Forecast workflow commits under data/forecast_issues/attempts/.
//
// Why a loader rather than an exported table: the ledger grows by a bot commit
// on every scheduled run, and those commits deliberately trigger neither CI nor
// a deploy. A table written by `gpa export` would go stale between human pushes
// and fail `gpa export --check` on the first one after it. Computing the counter
// when the site is built keeps it exact for the ledger the build saw, and the
// "as of" it carries is the latest attempt in that ledger, not the build clock,
// so the page states how current the count is instead of implying it is live.
//
// Every delivery day an arm was attempted for is counted once, at the best
// outcome any of its attempts reached. `already_issued` is a backstop run
// finding the day on record, so it confirms an issue and never counts as one.

import {existsSync, readFileSync, readdirSync} from "node:fs";
import {fileURLToPath} from "node:url";

const root = fileURLToPath(new URL("../../data/forecast_issues/attempts/", import.meta.url));
const rank = {issued: 5, partial: 4, abstained: 3, late: 2, failed: 1, started: 0};
const order = ["ridge", "ridge_da", "naive_previous_day", "naive_previous_week", "naive_similar_day"];

const read = (path) => JSON.parse(readFileSync(path, "utf8"));
const attempts = (existsSync(root) ? readdirSync(root) : [])
  .filter((id) => existsSync(`${root}${id}/started.json`))
  .map((id) => {
    const started = read(`${root}${id}/started.json`);
    const done = `${root}${id}/completed.json`;
    return {...started, ...(existsSync(done) ? read(done) : {status: "started"})};
  });

const days = new Map(); // model -> Map(delivery_date -> best status)
let asOf = null;
for (const a of attempts) {
  const stamp = a.completed_at ?? a.started_at;
  if (!asOf || stamp > asOf) asOf = stamp;
  if (!(a.status in rank)) continue;
  const perModel = days.get(a.model) ?? new Map();
  const best = perModel.get(a.delivery_date);
  if (best === undefined || rank[a.status] > rank[best]) perModel.set(a.delivery_date, a.status);
  days.set(a.model, perModel);
}

const arms = [...days.keys()]
  .sort((a, b) => (order.indexOf(a) + 1 || 99) - (order.indexOf(b) + 1 || 99) || a.localeCompare(b))
  .map((model) => {
    const outcomes = [...days.get(model).values()];
    const count = (status) => outcomes.filter((s) => s === status).length;
    const dates = [...days.get(model).keys()].sort();
    return {
      model,
      days: outcomes.length,
      issued: count("issued"),
      partial: count("partial"),
      abstained: count("abstained"),
      late: count("late"),
      failed: count("failed") + count("started"),
      first_delivery: dates[0],
      last_delivery: dates.at(-1)
    };
  });

process.stdout.write(JSON.stringify({as_of: asOf, attempts: attempts.length, arms}, null, 2));
