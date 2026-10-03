export function chinaDate(value = new Date()) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(value);
  const part = (type) => parts.find((item) => item.type === type).value;
  return `${part("year")}-${part("month")}-${part("day")}`;
}

export function shiftDate(value, days) {
  const date = new Date(`${value}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

export function matchStart(match) {
  return new Date(`${match.start_time?.replace(" ", "T")}+08:00`);
}

export function matchesOnScheduleDate(rows, date) {
  // API timestamps are Beijing wall time, shared by the calendar and voting popup.
  return rows.filter((match) => String(match.start_time || "").slice(0, 10) === date)
    .sort((a, b) => String(a.start_time).localeCompare(String(b.start_time)));
}

export function firstAvailableDay(rows, date) {
  for (let offset = 0; offset <= 7; offset += 1) {
    const day = shiftDate(date, offset);
    const matches = matchesOnScheduleDate(rows, day);
    if (matches.length) return { date: day, matches };
  }
  for (let offset = 1; offset <= 7; offset += 1) {
    const day = shiftDate(date, -offset);
    const matches = matchesOnScheduleDate(rows, day);
    if (matches.length) return { date: day, matches };
  }
  return { date, matches: [] };
}
