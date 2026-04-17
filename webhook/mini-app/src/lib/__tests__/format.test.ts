import { formatRelativeTime, formatDuration } from "../format";

describe("formatRelativeTime", () => {
  test("returns 'agora' for recent timestamps", () => {
    const now = new Date().toISOString();
    expect(formatRelativeTime(now)).toBe("agora");
  });

  test("returns minutes for < 1 hour", () => {
    const date = new Date(Date.now() - 15 * 60 * 1000).toISOString();
    expect(formatRelativeTime(date)).toBe("15min");
  });

  test("returns hours for < 24 hours", () => {
    const date = new Date(Date.now() - 3 * 3600 * 1000).toISOString();
    expect(formatRelativeTime(date)).toBe("3h");
  });

  test("returns days for < 7 days", () => {
    const date = new Date(Date.now() - 2 * 86400 * 1000).toISOString();
    expect(formatRelativeTime(date)).toBe("2d");
  });
});

describe("formatDuration", () => {
  test("formats seconds", () => {
    expect(formatDuration(45)).toBe("45s");
  });

  test("formats minutes and seconds", () => {
    expect(formatDuration(125)).toBe("2m 5s");
  });

  test("formats exact minutes", () => {
    expect(formatDuration(120)).toBe("2m");
  });
});
