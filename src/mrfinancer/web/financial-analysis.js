const metrics = {
  turnover: ["Revenue", "turnover", "sales", "sales revenue"],
  net_profit: ["Net profit", "net income", "net loss", "profit", "profit after tax", "earnings"],
  profit_before_tax: ["Profit before tax", "pre-tax profit", "pretax income", "pbt"],
  operating_profit: ["Operating profit", "operating income", "operating loss"],
  net_assets: ["Equity / net assets", "equity", "net assets", "shareholders' equity"],
  total_assets: ["Total assets", "assets"],
  total_liabilities: ["Total liabilities", "liabilities"],
  cash: ["Cash", "cash and cash equivalents"],
  employees: ["Employees", "employee count", "number of employees", "headcount"],
  authorised_capital: ["Authorised capital", "authorized capital"],
  paid_up_capital: ["Paid-up capital", "paid up capital"],
  total_funding: ["Total funding", "funding"],
  last_round_amount: ["Last round amount", "last funding round"],
  valuation: ["Valuation"],
};
const metricByPhrase = new Map(Object.entries(metrics).flatMap(([key, names]) =>
  names.map(name => [name.toLowerCase(), key])));
const basisLabels = { official: "Official", third_party: "Third-party", estimate: "Estimate", unverified: "Unverified" };
const balanceMetrics = new Set(["total_assets", "total_liabilities", "net_assets", "cash"]);
const percent = new Intl.NumberFormat(undefined, { style: "percent", maximumFractionDigits: 1 });
const calculatedNumber = new Intl.NumberFormat(undefined, { maximumSignificantDigits: 15 });
// Accommodate 52- and 53-week fiscal years and leap days.
const comparableSpanTolerance = 8 * 86400000;
const period = String.raw`\d{4}(?:-\d{2}-\d{2})?`;
const questionPeriods = new RegExp(String.raw`\s+(?:(?:in|for|as of|at|from|between)\s+)?(?:fy\s*)?(${period})(?:\s+(?:and|to|vs\.?|versus)\s+(?:fy\s*)?(${period}))?$`);

export const financialNumber = new Intl.NumberFormat(undefined, { maximumSignificantDigits: 21 });

export function financialLabel(metric) {
  return metrics[metric]?.[0] || metric.replaceAll("_", " ");
}

export function financialPeriod(fact) {
  if (fact.period_start && fact.period_end) return `${fact.period_start} to ${fact.period_end}`;
  return fact.period_end || "Period unstated";
}

export function financialBasis(fact) {
  return fact.as_filed ? "As filed" : basisLabels[fact.provenance.trust] || "Published";
}

export function chartable(fact) {
  return Number.isFinite(fact.value) && !fact.issues.some(issue => issue.severity === "reject");
}

export function financialPercent(fraction) {
  if (fraction > 0 && fraction < 0.001) return `<${percent.format(0.001)}`;
  if (fraction > 0.999 && fraction < 1) return `>${percent.format(0.999)}`;
  return percent.format(fraction);
}

export function financialAmount(value, fact, calculated = false) {
  const number = calculated ? calculatedNumber : financialNumber;
  return [fact.currency, number.format(value), fact.unit === "count" ? null : fact.unit].filter(Boolean).join(" ");
}

function sameFigure(facts) {
  const first = facts[0];
  return first && facts.every(fact => fact.value === first.value && fact.period_start === first.period_start &&
    fact.period_end === first.period_end) ? first : null;
}

function groupBy(facts, keyOf) {
  const groups = new Map();
  for (const fact of facts) {
    const key = keyOf(fact);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(fact);
  }
  return groups;
}

export function balanceSheetCompositions(facts) {
  const filed = facts.filter(fact => balanceMetrics.has(fact.metric) && chartable(fact) && fact.as_filed &&
    fact.period_end && fact.currency && fact.provenance.url && !fact.issues.some(issue => issue.severity === "warn"));
  const snapshots = groupBy(filed, fact => JSON.stringify([fact.period_end, fact.currency, fact.unit,
    fact.statement_scope, fact.provenance.source, fact.provenance.url]));
  const usable = [];
  for (const snapshot of snapshots.values()) {
    const concepts = groupBy(snapshot, fact => fact.metric);
    const figure = metric => sameFigure(concepts.get(metric) || []);
    const assets = figure("total_assets");
    if (!assets || assets.value <= 0) continue;

    const charts = [];
    const cash = figure("cash");
    if (cash && cash.value >= 0 && cash.value <= assets.value) {
      charts.push({ title: "Asset composition", slices: [
        { label: "Cash", value: cash.value },
        { label: "Other assets", value: assets.value - cash.value, calculated: true },
      ], facts: [assets, cash] });
    }

    const liabilities = figure("total_liabilities");
    const equity = figure("net_assets");
    if (liabilities?.value >= 0 && equity?.value >= 0) {
      const sum = liabilities.value + equity.value;
      if (Math.abs(sum - assets.value) <= assets.value * Number.EPSILON * 8) {
        charts.push({ title: "How assets are financed", slices: [
          { label: "Liabilities", value: liabilities.value },
          { label: "Equity", value: equity.value },
        ], facts: [assets, liabilities, equity] });
      }
    }

    if (charts.length) usable.push({ assets, charts });
  }
  return usable.sort((a, b) => b.assets.period_end.localeCompare(a.assets.period_end) ||
    Number(b.assets.statement_scope === "consolidated") - Number(a.assets.statement_scope === "consolidated"));
}

const endingIn = (fact, requested) => fact.period_end?.startsWith(requested);
const latestFirst = (a, b) => b.period_end.localeCompare(a.period_end);

function requestedFigures(facts, requested) {
  if (requested) return facts.filter(fact => endingIn(fact, requested));
  return facts.filter(fact => fact.period_end === facts[0]?.period_end);
}

function comparablePeriods(older, newer) {
  if (!older || !newer || older.period_end >= newer.period_end) return false;
  if (!older.period_start || !newer.period_start) return older.period_start === newer.period_start;
  const span = fact => Date.parse(fact.period_end) - Date.parse(fact.period_start);
  return Math.abs(span(older) - span(newer)) <= comparableSpanTolerance;
}

function changeInMetric(facts, requested) {
  const groups = groupBy(facts, fact => JSON.stringify([fact.currency, fact.unit, fact.statement_scope,
    fact.as_filed, fact.provenance.trust, fact.provenance.source]));
  const results = [];
  const [earlierPeriod, laterPeriod] = [...requested].sort();
  for (const group of groups.values()) {
    const newer = sameFigure(requestedFigures(group, laterPeriod || earlierPeriod));
    if (!newer) continue;
    const earlier = group.filter(fact => fact.period_end < newer.period_end);
    const older = sameFigure(requestedFigures(earlier, laterPeriod ? earlierPeriod : null));
    if (!comparablePeriods(older, newer)) continue;

    const change = newer.value - older.value;
    if (!Number.isFinite(change)) continue;
    const direction = change > 0 ? "Increased" : "Decreased";
    const fraction = Math.abs(change / older.value);
    const relative = older.value > 0 && Number.isFinite(fraction) ? ` (${percent.format(fraction)})` : "";
    const summary = change === 0 ? "No change" :
      `${direction} by ${financialAmount(Math.abs(change), newer, true)}${relative}`;
    results.push({ summary, facts: [older, newer] });
  }
  return results;
}

function answerAssetShare(sliceName, snapshots, requested) {
  const shares = [];
  for (const { assets, charts } of snapshots) {
    if (requested && !endingIn(assets, requested)) continue;
    if (!requested && shares.length && assets.period_end !== shares[0].facts[0].period_end) break;
    for (const { slices, facts } of charts) {
      const slice = slices.find(item => item.label.toLowerCase() === sliceName);
      if (slice) shares.push({ summary: financialPercent(slice.value / assets.value), facts });
    }
  }
  return shares.length ? { message: `${sliceName[0].toUpperCase()}${sliceName.slice(1)} as a share of assets`, items: shares } :
    { message: "The fetched filings have no matching balance-sheet figures for that share.", items: [] };
}

export function answerFinancialQuestion(question, facts, snapshots = []) {
  const normalized = question.toLowerCase().trim().replace(/[?!.]+$/, "").replace(/[’]/g, "'").replace(/\s+/g, " ");
  const dates = normalized.match(questionPeriods);
  const phrase = (dates ? normalized.slice(0, dates.index) : normalized).trim();
  const requested = dates ? dates.slice(1).filter(Boolean) : [];
  const share = phrase.match(/^what (?:share|percentage|percent) of (?:total )?assets (?:was|were|is|are) (?:(?:held in|funded by|financed by) )?(cash|liabilities|equity)$/);
  if (share && requested.length <= 1) return answerAssetShare(share[1], snapshots, requested[0]);
  const change = phrase.match(/^(?:how (?:did|much did) (.+) change|(?:compare|change in) (.+)|(.+) change)$/);
  const reported = phrase.match(/^how (?:many|much) (.+) did (?:it|the company) (?:have|make|report)$/);
  const name = (change?.slice(1).find(Boolean) || reported?.[1] || phrase)
    .replace(/^(?:what (?:was|were|is|are)|how much (?:was|is)|how many|show(?: me)?|give me) /, "")
    .replace(/^the /, "").replace(/^company's /, "").replace(/^(?:latest|most recent) /, "");
  const metric = metricByPhrase.get(name);
  if (!metric) return { message: "Ask for a figure or a change between reporting periods. Try one of the example questions.", items: [] };

  const matching = facts.filter(fact => fact.metric === metric && fact.period_end && chartable(fact)).sort(latestFirst);
  const label = financialLabel(metric);
  if (!matching.length) return { message: `The fetched records have no figure for ${label.toLowerCase()}.`, items: [] };
  if (change || requested.length === 2) {
    const items = changeInMetric(matching, requested);
    return { message: items.length ? `${label} change` :
      `I couldn’t match two comparable ${label.toLowerCase()} figures. Try asking with exact period-end dates.`, items };
  }
  const selected = requestedFigures(matching, requested[0]);
  return selected.length ? { message: label, items: [{ facts: selected }] } :
    { message: `The fetched records have no ${label.toLowerCase()} figure for ${requested[0]}.`, items: [] };
}

export function financialQuestionExamples(facts, snapshots) {
  const available = facts.filter(fact => fact.period_end && chartable(fact)).sort(latestFirst);
  const first = available.find(fact => fact.metric === "turnover") || available[0];
  if (!first) return [];

  const examples = [`What was ${financialLabel(first.metric).toLowerCase()} in ${first.period_end.slice(0, 4)}?`];
  const choices = new Set([first.metric, ...available.map(fact => fact.metric)]);
  for (const metric of choices) {
    const question = `How did ${financialLabel(metric).toLowerCase()} change?`;
    if (answerFinancialQuestion(question, available).items.length) {
      examples.push(question);
      break;
    }
  }
  const slice = snapshots[0]?.charts[0].slices[0].label;
  if (slice) examples.push(slice === "Cash" ? "What share of assets was cash?" : "What share of assets was funded by liabilities?");
  return examples;
}
