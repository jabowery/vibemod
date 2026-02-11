defmodule TLinda.TupleSpace do
  @moduledoc """
  Global Tuple Space implementing Linda/TLinda operations with correct semantics.

  ## Tuple representation

  Tuples are plain Elixir tuples or lists.  A "pattern" is a tuple/list whose
  fields are either literal values (matched by equality) or the atom `:_`
  (wildcard, matches any value).  After a successful match the *concrete*
  tuple from TS is returned so the caller can pattern-match / destructure it
  in Elixir — this is the equivalent of TLinda's "?var binding".

  ## Operations

  | TLinda op        | Function              | Blocks until                         |
  |------------------|-----------------------|--------------------------------------|
  | Out(T)           | `out/1`               | never                                |
  | Rd(T)            | `rd/1`                | T matches something present          |
  | In(T)            | `in_op/1`             | T matches; atomically removes match  |
  | Co(U,..,V)       | `co/1`                | ALL patterns simultaneously present  |
  | NotCo(U,..,V)    | `not_co/1`            | at least ONE pattern absent          |
  | AntiRd(T)        | `anti_rd/1`           | T has NO match present               |
  | AntiCo(U,V)      | `anti_co/1`           | ALL patterns absent                  |
  | AntiNotCo(U,V)   | `anti_not_co/1`       | NOT(NotCo) = ALL present (§6.1)      |
  | Opposite(A,B)    | `opposite/2`          | never — declares non-co-excludable   |

  Each blocking op has a predicate ("one-shot") variant suffixed `p` that
  returns `{:ok, value}` or `false` without blocking.

  ## Subscriptions

  `subscribe/1` registers the calling process to receive
  `{:ts_event, :out | :in, tuple}` messages on every Out / In that
  matches the given pattern.  Used by Event Windows.
  """
  use GenServer

  # ─── public api ────────────────────────────────────────────────────

  def start_link(_opts \\ []),
    do: GenServer.start_link(__MODULE__, :ok, name: __MODULE__)

  # --- Out / Remove ---

  @doc "Out(T): assert tuple T into tuple space."
  def out(tuple), do: GenServer.call(__MODULE__, {:out, tuple})

  @doc "Remove one copy of T (retract). No-op if absent."
  def remove(tuple), do: GenServer.call(__MODULE__, {:remove, tuple})

  # --- Rd / In (blocking) ---

  @doc "Rd(T): block until a tuple matching pattern is present; return it."
  def rd(pattern) do
    ref = make_ref()
    GenServer.cast(__MODULE__, {:wait, :rd, pattern, self(), ref})
    receive do {:ts_reply, ^ref, val} -> val end
  end

  @doc "In(T): like Rd but atomically removes the matched tuple."
  def in_op(pattern) do
    ref = make_ref()
    GenServer.cast(__MODULE__, {:wait, :in, pattern, self(), ref})
    receive do {:ts_reply, ^ref, val} -> val end
  end

  # --- Co / NotCo / AntiRd / AntiCo (blocking) ---

  @doc "Co(patterns): block until ALL patterns are simultaneously present."
  def co(patterns) when is_list(patterns) do
    ref = make_ref()
    GenServer.cast(__MODULE__, {:wait, :co, patterns, self(), ref})
    receive do {:ts_reply, ^ref, vals} -> vals end
  end
  def co(a, b), do: co([a, b])
  def co(a, b, c), do: co([a, b, c])

  @doc "NotCo(patterns): block until at least one pattern has no match."
  def not_co(patterns) when is_list(patterns) do
    ref = make_ref()
    GenServer.cast(__MODULE__, {:wait, :not_co, patterns, self(), ref})
    receive do {:ts_reply, ^ref, _} -> :ok end
  end
  def not_co(a, b), do: not_co([a, b])
  def not_co(a, b, c), do: not_co([a, b, c])
  def not_co(a, b, c, d), do: not_co([a, b, c, d])

  @doc "AntiRd(T): block until T has NO match in TS."
  def anti_rd(pattern) do
    ref = make_ref()
    GenServer.cast(__MODULE__, {:wait, :anti_rd, pattern, self(), ref})
    receive do {:ts_reply, ^ref, _} -> :ok end
  end

  @doc "AntiCo(patterns): block until ALL patterns are absent."
  def anti_co(patterns) when is_list(patterns) do
    ref = make_ref()
    GenServer.cast(__MODULE__, {:wait, :anti_co, patterns, self(), ref})
    receive do {:ts_reply, ^ref, _} -> :ok end
  end
  def anti_co(a, b), do: anti_co([a, b])

  @doc """
  AntiNotCo(patterns): block until NOT(NotCo), i.e. until ALL patterns
  are simultaneously present.

  Semantically equivalent to Co in steady state, but distinct in the
  Appendix formalism: Co blocks until first co-occurrence; AntiNotCo
  blocks until "it is no longer the case that at least one is absent."

  Used in MetaSensorRel (§6.1) where the Appendix writes:
    "AntiNotCo S1Rel, S2Rel" — wait until both parent relevances co-occur.
  """
  def anti_not_co(patterns) when is_list(patterns) do
    ref = make_ref()
    GenServer.cast(__MODULE__, {:wait, :anti_not_co, patterns, self(), ref})
    receive do {:ts_reply, ^ref, vals} -> vals end
  end
  def anti_not_co(a, b), do: anti_not_co([a, b])

  # --- Predicate (non-blocking) variants ---

  @doc "Rdp(T): non-blocking Rd.  `{:ok, tuple}` or `false`."
  def rdp(pattern), do: GenServer.call(__MODULE__, {:rdp, pattern})

  @doc "Inp(T): non-blocking In.  `{:ok, tuple}` or `false`."
  def inp(pattern), do: GenServer.call(__MODULE__, {:inp, pattern})

  @doc "Cop(patterns): non-blocking Co.  `{:ok, [tuples]}` or `false`."
  def cop(patterns) when is_list(patterns),
    do: GenServer.call(__MODULE__, {:cop, patterns})
  def cop(a, b), do: cop([a, b])

  @doc "NotCop: true if at least one pattern absent."
  def not_cop(patterns) when is_list(patterns),
    do: GenServer.call(__MODULE__, {:not_cop, patterns})

  @doc "AntiCop: true if ALL patterns absent."
  def anti_cop(patterns) when is_list(patterns),
    do: GenServer.call(__MODULE__, {:anti_cop, patterns})
  def anti_cop(a, b), do: anti_cop([a, b])

  @doc """
  AntiNotCop: non-blocking AntiNotCo.
  Returns `{:ok, [tuples]}` if all patterns present, `false` otherwise.
  Semantically: NOT(NotCo) as a predicate = "are all co-present?"
  """
  def anti_not_cop(patterns) when is_list(patterns),
    do: GenServer.call(__MODULE__, {:anti_not_cop, patterns})
  def anti_not_cop(a, b), do: anti_not_cop([a, b])

  # --- Opposite / Subscribe / Dump ---

  def opposite(a, b), do: GenServer.call(__MODULE__, {:opposite, a, b})
  def opposite?(a, b), do: GenServer.call(__MODULE__, {:opposite?, a, b})

  @doc """
  Subscribe to TS events matching `pattern`.
  Subscriber receives `{:ts_event, :out | :in, concrete_tuple}`.
  """
  def subscribe(pattern), do: GenServer.call(__MODULE__, {:subscribe, pattern, self()})

  def dump, do: GenServer.call(__MODULE__, :dump)

  @doc "Clear all tuples, waiters, opposites, subscribers. Hard reset."
  def reset, do: GenServer.call(__MODULE__, :reset)

  # ─── server state ──────────────────────────────────────────────────

  defmodule S do
    @moduledoc false
    defstruct tuples: nil,          # ETS: {tuple, count}
              waiters: [],          # [{type, data, pid, ref}]
              opposites: MapSet.new(),
              subscribers: []       # [{pattern, pid}]
  end

  # ─── callbacks ─────────────────────────────────────────────────────

  @impl true
  def init(:ok) do
    tab = :ets.new(:ts, [:set, :public, :named_table])
    {:ok, %S{tuples: tab}}
  end

  # --- synchronous (call) handlers ---

  @impl true
  def handle_call({:out, t}, _from, s) do
    do_out(t, s)
    s = try_wake(s)
    {:reply, :ok, s}
  end

  def handle_call({:remove, t}, _from, s) do
    do_remove(t, s)
    s = try_wake(s)
    {:reply, :ok, s}
  end

  def handle_call({:rdp, p}, _from, s) do
    {:reply, (if m = find(p, s), do: {:ok, m}, else: false), s}
  end

  def handle_call({:inp, p}, _from, s) do
    case find(p, s) do
      nil -> {:reply, false, s}
      m   -> do_remove(m, s); {:reply, {:ok, m}, try_wake(s)}
    end
  end

  def handle_call({:cop, ps}, _from, s) do
    if all_present?(ps, s),
      do: {:reply, {:ok, Enum.map(ps, &find(&1, s))}, s},
      else: {:reply, false, s}
  end

  def handle_call({:not_cop, ps}, _from, s) do
    {:reply, not all_present?(ps, s), s}
  end

  def handle_call({:anti_cop, ps}, _from, s) do
    {:reply, all_absent?(ps, s), s}
  end

  def handle_call({:anti_not_cop, ps}, _from, s) do
    # AntiNotCop = NOT(NotCo) = all present?
    if all_present?(ps, s),
      do: {:reply, {:ok, Enum.map(ps, &find(&1, s))}, s},
      else: {:reply, false, s}
  end

  def handle_call({:opposite, a, b}, _from, s) do
    ops = s.opposites |> MapSet.put({a, b}) |> MapSet.put({b, a})
    {:reply, :ok, %{s | opposites: ops}}
  end

    def handle_call({:opposite?, a, b}, _from, s) do
    {:reply, :ets.member(s.opposites_tab, {a, b}), s}
  end

  def handle_call({:subscribe, p, pid}, _from, s) do
    Process.monitor(pid)
    {:reply, :ok, %{s | subscribers: [{p, pid} | s.subscribers]}}
  end

  def handle_call(:dump, _from, s) do
    {:reply, :ets.tab2list(s.tuples), s}
  end

  def handle_call(:reset, _from, s) do
    :ets.delete_all_objects(s.tuples)
    {:reply, :ok, %S{tuples: s.tuples}}
  end

  # --- asynchronous (cast) handlers: register waiters ---

  @impl true
  def handle_cast({:wait, type, data, pid, ref}, s) do
    case try_satisfy(type, data, s) do
      {:yes, val, s2} ->
        send(pid, {:ts_reply, ref, val})
        {:noreply, s2}
      :no ->
        {:noreply, %{s | waiters: s.waiters ++ [{type, data, pid, ref}]}}
    end
  end

  @impl true
  def handle_info({:DOWN, _ref, :process, pid, _}, s) do
    ws = Enum.reject(s.waiters, fn {_, _, p, _} -> p == pid end)
    ss = Enum.reject(s.subscribers, fn {_, p} -> p == pid end)
    {:noreply, %{s | waiters: ws, subscribers: ss}}
  end

  def handle_info(_, s), do: {:noreply, s}

  # ─── internals ─────────────────────────────────────────────────────

  defp do_out(t, s) do
    case :ets.lookup(s.tuples, t) do
      [{^t, c}] -> :ets.insert(s.tuples, {t, c + 1})
      []        -> :ets.insert(s.tuples, {t, 1})
    end
    notify(s, :out, t)
  end

  defp do_remove(t, s) do
    case :ets.lookup(s.tuples, t) do
      [{^t, c}] when c > 1 -> :ets.insert(s.tuples, {t, c - 1})
      [{^t, _}]            -> :ets.delete(s.tuples, t)
      []                   -> :ok
    end
    notify(s, :in, t)
  end

  # Pattern matching: every non-`:_` field must equal.
  #
  # §7.4 Deny-wildcard: if a STORED tuple contains {:dw, V} in a field,
  # then a pattern's :_ at that position does NOT match.  The pattern
  # must supply {:dw, V} exactly.  This prevents "fishing expeditions"
  # where a rogue process uses wildcards to discover Action names.
  #
  # In a pattern, {:dw, V} matches {:dw, V} in the tuple (exact).
  # In a pattern, :_ matches anything EXCEPT {:dw, _} in the tuple.

  defp matches?(pat, tup) when is_tuple(pat) and is_tuple(tup) do
    tuple_size(pat) == tuple_size(tup) and
      Enum.zip(Tuple.to_list(pat), Tuple.to_list(tup))
      |> Enum.all?(fn
        {:_, {:dw, _}} -> false           # §7.4: wildcard blocked
        {:_, _} -> true                    # normal wildcard
        {a, b}  -> deep_match?(a, b)
      end)
  end
  defp matches?(a, a), do: true
  defp matches?(_, _), do: false

  # Recurse into nested tuples.  Deny-wildcard applies at every depth.
  defp deep_match?(:_, {:dw, _}), do: false     # §7.4
  defp deep_match?(:_, _), do: true
  defp deep_match?(a, b) when is_tuple(a) and is_tuple(b), do: matches?(a, b)
  defp deep_match?(a, a), do: true
  defp deep_match?(_, _), do: false

  defp find(pat, s) do
    :ets.foldl(fn
      {t, c}, nil when c > 0 -> if matches?(pat, t), do: t, else: nil
      _, acc -> acc
    end, nil, s.tuples)
  end

  defp all_present?(ps, s), do: Enum.all?(ps, &(find(&1, s) != nil))
  defp all_absent?(ps, s),  do: Enum.all?(ps, &(find(&1, s) == nil))

  # Try to satisfy a single waiter immediately.
  defp try_satisfy(:rd, pat, s) do
    case find(pat, s) do nil -> :no; m -> {:yes, m, s} end
  end

  defp try_satisfy(:in, pat, s) do
    case find(pat, s) do
      nil -> :no
      m   -> do_remove(m, s); {:yes, m, try_wake(s)}
    end
  end

  defp try_satisfy(:co, pats, s) do
    if all_present?(pats, s),
      do: {:yes, Enum.map(pats, &find(&1, s)), s},
      else: :no
  end

  defp try_satisfy(:not_co, pats, s) do
    if not all_present?(pats, s), do: {:yes, :ok, s}, else: :no
  end

  defp try_satisfy(:anti_rd, pat, s) do
    if find(pat, s) == nil, do: {:yes, :ok, s}, else: :no
  end

  defp try_satisfy(:anti_co, pats, s) do
    if all_absent?(pats, s), do: {:yes, :ok, s}, else: :no
  end

  # AntiNotCo = NOT(NotCo) = all present — same satisfaction as :co
  # but kept as a distinct waiter type for semantic fidelity to the Appendix
  defp try_satisfy(:anti_not_co, pats, s) do
    if all_present?(pats, s),
      do: {:yes, Enum.map(pats, &find(&1, s)), s},
      else: :no
  end

  # Wake all waiters whose conditions are now met.
  defp try_wake(s) do
    {sat, rem} =
      Enum.split_with(s.waiters, fn {type, data, _pid, _ref} ->
        case try_satisfy(type, data, s) do
          {:yes, _, _} -> true
          :no -> false
        end
      end)

    # Actually deliver results (some :in waiters mutate TS).
    s2 = %{s | waiters: rem}
    Enum.reduce(sat, s2, fn {type, data, pid, ref}, acc ->
      case try_satisfy(type, data, acc) do
        {:yes, val, acc2} ->
          send(pid, {:ts_reply, ref, val})
          acc2
        :no ->
          # Another waiter consumed it first; re-queue.
          %{acc | waiters: acc.waiters ++ [{type, data, pid, ref}]}
      end
    end)
  end

  defp notify(s, event, tuple) do
    for {pat, pid} <- s.subscribers, matches?(pat, tuple) do
      send(pid, {:ts_event, event, tuple})
    end
  end
end
