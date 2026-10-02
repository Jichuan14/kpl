export function browserDate(value = new Date()) {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
}

export function shiftDate(value, days) {
  const date = new Date(`${value}T12:00:00`);
  date.setDate(date.getDate() + days);
  return browserDate(date);
}

export function matchStart(match) {
  return new Date(`${match.start_time?.replace(" ", "T")}+08:00`);
}

export function matchesOnLocalDate(rows, date) {
  return rows.filter((match) => browserDate(matchStart(match)) === date)
    .sort((a, b) => String(a.start_time).localeCompare(String(b.start_time)));
}

export function firstAvailableDay(rows, date, now = Date.now()) {
  for (let offset = 0; offset <= 7; offset += 1) {
    const day = shiftDate(date, offset);
    const matches = matchesOnLocalDate(rows, day).filter((match) => offset !== 0 || matchStart(match).getTime() >= now);
    if (matches.length) return { date: day, matches };
  }
  for (let offset = 1; offset <= 7; offset += 1) {
    const day = shiftDate(date, -offset);
    const matches = matchesOnLocalDate(rows, day);
    if (matches.length) return { date: day, matches };
  }
  return { date, matches: [] };
}
