// Fixed calendar-offset helpers for the year-over-year crop-stress flow.
// Same calendar window one year prior — hemisphere-agnostic by construction,
// because it is a pure date offset, not a growing-season lookup.

function isLeapYear(y) {
  return (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;
}

// "2022-08-15" -> "2021-08-15"; clamps Feb 29 to Feb 28 in non-leap target years.
function oneYearBefore(iso) {
  if (!iso) return null;
  const [y, m, d] = iso.split("-").map(Number);
  const targetYear = y - 1;
  let day = d;
  if (m === 2 && d === 29 && !isLeapYear(targetYear)) day = 28;
  return `${targetYear}-${String(m).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

export { oneYearBefore };
