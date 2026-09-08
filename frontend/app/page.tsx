"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api, ApiError, type AssignmentWithCourse, type Course, type Me, type UrgencyReport } from "@/lib/api";
import { formatDueDate, outlineBadge, urgencyClasses } from "@/lib/format";

export default function DashboardPage() {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [courses, setCourses] = useState<Course[]>([]);
  const [assignments, setAssignments] = useState<AssignmentWithCourse[]>([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [urgency, setUrgency] = useState<UrgencyReport | null>(null);
  const [evaluating, setEvaluating] = useState(false);
  const [urgencyError, setUrgencyError] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    const [courseList, assignmentList] = await Promise.all([
      api.courses(),
      api.assignments({ upcoming: true }),
    ]);
    setCourses(courseList);
    setAssignments(assignmentList);
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const user = await api.me();
        setMe(user);
        await loadData();
        try {
          setUrgency(await api.urgencyReport());
        } catch {
          // Non-fatal: the dashboard still works without a stored report.
        }
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          router.push("/login");
          return;
        }
        setError("Could not reach the backend API. Is it running?");
      } finally {
        setLoading(false);
      }
    })();
  }, [loadData, router]);

  async function handleSync() {
    setSyncing(true);
    setSyncMessage(null);
    setError(null);
    try {
      const result = await api.sync();
      setSyncMessage(
        `Synced ${result.courses} courses, ${result.assignments} assignments, ${result.outlines_found} outlines found.`
      );
      await loadData();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Sync failed");
    } finally {
      setSyncing(false);
    }
  }

  async function handleEvaluate() {
    setEvaluating(true);
    setUrgencyError(null);
    try {
      setUrgency(await api.evaluateUrgency());
    } catch (err) {
      setUrgencyError(err instanceof ApiError ? err.message : "Evaluation failed");
    } finally {
      setEvaluating(false);
    }
  }

  async function handleLogout() {
    await api.logout();
    router.push("/login");
  }

  if (loading) return <p className="text-sm text-slate-500">Loading...</p>;

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-800">Welcome, {me?.name}</h1>
          <p className="text-sm text-slate-500">Pulled straight from canvas.ualberta.ca</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleSync}
            disabled={syncing}
            className="rounded bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-50"
          >
            {syncing ? "Syncing..." : "Sync from Canvas"}
          </button>
          <button
            onClick={handleLogout}
            className="rounded border border-slate-300 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100"
          >
            Log out
          </button>
        </div>
      </div>

      {syncMessage && <p className="rounded bg-emerald-50 px-3 py-2 text-sm text-emerald-700">{syncMessage}</p>}
      {error && <p className="rounded bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}

      <section className="rounded-lg border border-indigo-200 bg-indigo-50/40 p-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h2 className="font-medium text-slate-800">Most urgent assignments</h2>
            <p className="text-xs text-slate-500">
              {urgency
                ? `Last evaluated ${new Date(urgency.generated_at).toLocaleString()}`
                : "Ranked by deadline, course difficulty, and assignment weighting"}
            </p>
          </div>
          <button
            onClick={handleEvaluate}
            disabled={evaluating}
            className="shrink-0 rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            {evaluating ? "Evaluating..." : urgency ? "Re-evaluate" : "Evaluate my assignments"}
          </button>
        </div>

        {urgencyError && (
          <p className="mt-3 rounded bg-red-50 px-3 py-2 text-sm text-red-700">{urgencyError}</p>
        )}

        {!urgency && !urgencyError && !evaluating && (
          <p className="mt-3 text-sm text-slate-500">
            Click &ldquo;Evaluate my assignments&rdquo; to have the agent read your deadlines, course
            outlines, and course descriptions, and rank what to work on first.
          </p>
        )}

        {urgency && urgency.top_assignments.length > 0 && (
          <ol className="mt-3 space-y-2">
            {urgency.top_assignments.map((a, i) => (
              <li
                key={a.assignment_id}
                className="flex gap-3 rounded-lg border border-slate-200 bg-white p-3"
              >
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-indigo-100 text-xs font-semibold text-indigo-700">
                  {i + 1}
                </span>
                <div>
                  <p className="text-sm font-medium text-slate-800">
                    {a.name}
                    {a.course_code && <span className="font-normal text-slate-500"> · {a.course_code}</span>}
                  </p>
                  <p className="text-xs text-slate-500">{formatDueDate(a.due_at)}</p>
                  <p className="mt-1 text-sm text-slate-600">{a.reason}</p>
                </div>
              </li>
            ))}
          </ol>
        )}
      </section>

      <section>
        <h2 className="mb-3 text-lg font-medium text-slate-800">Courses</h2>
        {courses.length === 0 ? (
          <p className="text-sm text-slate-500">
            No courses yet — click &ldquo;Sync from Canvas&rdquo; to pull your enrollments.
          </p>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2">
            {courses.map((c) => {
              const badge = outlineBadge(c.outline?.confidence ?? 0);
              return (
                <Link
                  key={c.id}
                  href={`/courses/${c.id}`}
                  className="rounded-lg border border-slate-200 bg-white p-4 hover:border-slate-300 hover:shadow-sm"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <p className="font-medium text-slate-800">{c.name}</p>
                      <p className="text-xs text-slate-500">
                        {c.course_code} {c.term ? `· ${c.term}` : ""}
                      </p>
                    </div>
                    {c.difficulty && (
                      <span className="shrink-0 rounded bg-indigo-50 px-2 py-0.5 text-xs text-indigo-700">
                        Difficulty {c.difficulty}/5
                      </span>
                    )}
                  </div>
                  <span className={`mt-3 inline-block rounded px-2 py-0.5 text-xs ${badge.classes}`}>
                    {badge.label}
                  </span>
                </Link>
              );
            })}
          </div>
        )}
      </section>

      <section>
        <h2 className="mb-3 text-lg font-medium text-slate-800">Upcoming assignments</h2>
        {assignments.length === 0 ? (
          <p className="text-sm text-slate-500">No upcoming assignments found.</p>
        ) : (
          <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-50 text-xs uppercase text-slate-500">
                <tr>
                  <th className="px-4 py-2">Assignment</th>
                  <th className="px-4 py-2">Course</th>
                  <th className="px-4 py-2">Due</th>
                  <th className="px-4 py-2">Status</th>
                  <th className="px-4 py-2">Points</th>
                </tr>
              </thead>
              <tbody>
                {assignments.map((a) => (
                  <tr key={a.id} className="border-t border-slate-100">
                    <td className="px-4 py-2">
                      {a.html_url ? (
                        <a href={a.html_url} target="_blank" rel="noreferrer" className="text-blue-600 hover:underline">
                          {a.name}
                        </a>
                      ) : (
                        a.name
                      )}
                    </td>
                    <td className="px-4 py-2 text-slate-600">{a.course_code || a.course_name}</td>
                    <td className="px-4 py-2">
                      <span className={`rounded px-2 py-0.5 text-xs ${urgencyClasses(a.due_at)}`}>
                        {formatDueDate(a.due_at)}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-slate-600">{a.submission_status ?? "—"}</td>
                    <td className="px-4 py-2 text-slate-600">
                      {a.score != null ? `${a.score}/${a.points_possible ?? "?"}` : a.points_possible ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
