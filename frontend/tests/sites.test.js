import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import Sites from "../src/views/dashboard/Sites.vue";

const site = {
  id: 1,
  name: "web",
  domain: "web.example.com",
  site_type: "proxy",
  root_path: "",
  proxy_target: "http://127.0.0.1:3000",
  listen_port: 80,
  enabled: true,
  status: "simulated",
  updated_at: "2026-11-02T07:20:00",
};
const change = {
  id: 30,
  site_id: 1,
  site_name: "web",
  operation: "update",
  status: "pending",
  created_at: "2026-11-02T07:20:00",
  before: "<before>",
  after: "<after>",
  steps: [{ name: "write", status: "failed", message: "步骤失败或超时" }],
  message: "需要人工核查",
  simulated: false,
};
const preview = {
  token: "opaque-preview",
  expires_at: "2099-11-02T07:25:00Z",
  operation: "create",
  site_id: null,
  site_name: "new",
  before: null,
  after: "<script>alert(1)</script>",
  diff: "+<script>alert(1)</script>",
  simulated: true,
  warnings: ["配置可能包含敏感信息，未脱敏"],
};
const response = (data, status = 200) => ({
  ok: status < 400,
  status,
  json: async () => data,
});
const deferred = () => {
  let resolve;
  const promise = new Promise((r) => {
    resolve = r;
  });
  return { promise, resolve };
};
let wrapper;
let requests;
beforeEach(() => {
  const values = new Map();
  vi.stubGlobal("localStorage", {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  });
});
function setup(handler = () => undefined) {
  requests = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url, options = {}) => {
      const body = options.body ? JSON.parse(options.body) : undefined;
      requests.push({ url, ...options, body });
      const custom = handler(url, body, options);
      if (custom !== undefined) return custom;
      if (url === "/api/sites") return response({ items: [site] });
      if (url.startsWith("/api/site-changes?"))
        return response({ items: [change] });
      if (url.endsWith("/preview"))
        return response({
          ...preview,
          operation: body.operation,
          site_id: body.site_id ?? null,
          site_name: body.config?.name ?? "web",
        });
      if (url.endsWith("/execute"))
        return response({
          change: {
            ...change,
            status: "simulated",
            simulated: true,
            message: "模拟完成",
          },
          site,
        });
      throw new Error(`Unexpected URL ${url}`);
    }),
  );
  wrapper = mount(Sites);
  return wrapper;
}
function button(text) {
  const b = wrapper.findAll("button").find((b) => b.text() === text);
  expect(b, `button ${text}`).toBeTruthy();
  return b;
}
async function createForm() {
  await button("新增站点").trigger("click");
  await wrapper.get("#site-name").setValue("new");
  await wrapper.get("#site-domain").setValue("new.example.com");
  await wrapper.get("#proxy-target").setValue("http://127.0.0.1:3000");
}
const calls = (suffix) => requests.filter((r) => r.url.endsWith(suffix));
afterEach(() => {
  wrapper?.unmount();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("site change review", () => {
  it("submits only a preview and executes its opaque token only after a separate confirmation", async () => {
    setup();
    await flushPromises();
    await createForm();
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(calls("/preview")).toHaveLength(1);
    expect(calls("/preview")[0].body).toEqual({
      operation: "create",
      config: {
        name: "new",
        domain: "new.example.com",
        site_type: "proxy",
        root_path: "",
        proxy_target: "http://127.0.0.1:3000",
        listen_port: 80,
        enabled: true,
      },
    });
    expect(calls("/execute")).toHaveLength(0);
    expect(wrapper.text()).toContain("面板本机");
    expect(wrapper.text()).toContain("模拟模式");
    expect(wrapper.text()).toContain("2099");
    expect(wrapper.find("script").exists()).toBe(false);
    expect(wrapper.text()).toContain("+<script>alert(1)</script>");
    await button("确认执行变更").trigger("click");
    await flushPromises();
    expect(calls("/execute").map((r) => r.body)).toEqual([
      { token: "opaque-preview" },
    ]);
    expect(requests.filter((r) => r.url === "/api/sites")).toHaveLength(2);
    expect(
      requests.filter((r) => r.url.startsWith("/api/site-changes?")),
    ).toHaveLength(2);
    expect(wrapper.text()).toContain("模拟完成");
    expect(
      wrapper.findAll(".status-active").some((p) => p.text().includes("模拟")),
    ).toBe(false);
  });
  it("discards a displayed token when a field changes", async () => {
    setup();
    await flushPromises();
    await createForm();
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    await wrapper.get("#site-domain").setValue("changed.example.com");
    expect(
      wrapper.findAll("button").some((b) => b.text() === "确认执行变更"),
    ).toBe(false);
    expect(calls("/execute")).toHaveLength(0);
  });
  it("discards a delayed preview after edits and keeps the sent request snapshot unchanged", async () => {
    const waiting = deferred();
    setup((url) => (url.endsWith("/preview") ? waiting.promise : undefined));
    await flushPromises();
    await createForm();
    await wrapper.get("form").trigger("submit");
    await wrapper.get("#site-domain").setValue("changed.example.com");
    waiting.resolve(response(preview));
    await flushPromises();
    expect(calls("/preview")).toHaveLength(1);
    expect(calls("/preview")[0].body.config.domain).toBe("new.example.com");
    expect(
      wrapper.findAll("button").some((b) => b.text() === "确认执行变更"),
    ).toBe(false);
  });
  it("switching operations or cancelling removes confirmation and ignores an older preview response", async () => {
    const waiting = deferred();
    let count = 0;
    setup((url) =>
      url.endsWith("/preview") && ++count === 1 ? waiting.promise : undefined,
    );
    await flushPromises();
    await createForm();
    await wrapper.get("form").trigger("submit");
    await button("取消").trigger("click");
    await button("删除").trigger("click");
    await flushPromises();
    waiting.resolve(response(preview));
    await flushPromises();
    expect(calls("/preview")[1].body).toEqual({
      operation: "delete",
      site_id: 1,
    });
    expect(wrapper.get('[aria-label="待确认变更"]').text()).toContain("删除");
    expect(wrapper.get('[aria-label="待确认变更"]').text()).not.toContain(
      "new.example.com",
    );
    await button("取消变更").trigger("click");
    expect(
      wrapper.findAll("button").some((b) => b.text() === "确认执行变更"),
    ).toBe(false);
  });
  it.each(["生效", "删除"])(
    "%s uses preview and confirmation instead of legacy writes",
    async (action) => {
      setup();
      await flushPromises();
      await button(action).trigger("click");
      await flushPromises();
      expect(calls("/preview")[0].body).toEqual({
        operation: action === "删除" ? "delete" : "apply",
        site_id: 1,
      });
      expect(calls("/execute")).toHaveLength(0);
      await button("确认执行变更").trigger("click");
      await flushPromises();
      expect(calls("/execute")).toHaveLength(1);
      expect(
        requests.some((r) => r.url.startsWith("/api/sites") && r.method),
      ).toBe(false);
    },
  );
  it("allows only one preview per submission and one execute while locked, with inputs disabled", async () => {
    const waiting = deferred();
    const previewWaiting = deferred();
    setup((url) =>
      url.endsWith("/execute")
        ? waiting.promise
        : url.endsWith("/preview")
          ? previewWaiting.promise
          : undefined,
    );
    await flushPromises();
    await createForm();
    const f = wrapper.get("form");
    await f.trigger("submit");
    await f.trigger("submit");
    await flushPromises();
    expect(calls("/preview")).toHaveLength(1);
    previewWaiting.resolve(response(preview));
    await flushPromises();
    const confirm = button("确认执行变更");
    await confirm.trigger("click");
    await confirm.trigger("click");
    expect(calls("/execute")).toHaveLength(1);
    expect(wrapper.get("#site-name").element.disabled).toBe(true);
    expect(button("新增站点").element.disabled).toBe(true);
    waiting.resolve(
      response({
        change: { ...change, status: "applied", message: "已执行" },
        site,
      }),
    );
    await flushPromises();
  });
  it.each([
    [401, "预览凭证已过期，请重新预览"],
    [
      503,
      {
        message: "需人工核查",
        change_id: 30,
        status: "manual_review_required",
      },
    ],
    [
      422,
      [
        {
          loc: ["body", "config", "domain"],
          msg: "Value error, 域名不合法",
          type: "value_error",
        },
      ],
    ],
  ])(
    "renders %s failures, preserves history and never retries execute",
    async (status, detail) => {
      localStorage.setItem("panel_token", "login-token");
      setup((url) =>
        url.endsWith("/execute") ? response({ detail }, status) : undefined,
      );
      await flushPromises();
      await button("删除").trigger("click");
      await flushPromises();
      await button("确认执行变更").trigger("click");
      await flushPromises();
      expect(wrapper.get('[role="alert"]').text()).toContain(
        typeof detail === "string"
          ? "预览凭证已过期"
          : status === 503
            ? "需人工核查"
            : "域名不合法",
      );
      expect(wrapper.text()).not.toContain("[object Object]");
      expect(wrapper.get('[aria-label="变更历史"]').text()).toContain(
        "需要人工核查",
      );
      expect(calls("/execute")).toHaveLength(1);
      expect(localStorage.getItem("panel_token")).toBe("login-token");
      expect(
        wrapper.findAll("button").some((b) => b.text() === "确认执行变更"),
      ).toBe(false);
    },
  );
  it("keeps history after a refresh error and exposes before/after and steps as text", async () => {
    let count = 0;
    setup((url) =>
      url.startsWith("/api/site-changes?") && ++count > 1
        ? response({ detail: "历史暂不可用" }, 503)
        : undefined,
    );
    await flushPromises();
    await button("刷新历史").trigger("click");
    await flushPromises();
    const history = wrapper.get('[aria-label="变更历史"]');
    expect(history.text()).toContain("历史暂不可用");
    expect(history.text()).toContain("需要人工核查");
    expect(history.text()).toContain("<before>");
    expect(history.text()).toContain("步骤失败或超时");
    expect(history.text()).not.toContain("暂无变更记录");
  });
  it("serializes history refresh against pagination so an older page cannot overwrite a fresh list", async () => {
    const waiting = deferred();
    let count = 0;
    setup((url) => {
      if (!url.startsWith("/api/site-changes?")) return undefined;
      count++;
      if (count === 1)
        return response({
          items: Array.from({ length: 20 }, (_, i) => ({
            ...change,
            id: 40 - i,
          })),
        });
      return waiting.promise;
    });
    await flushPromises();
    await button("加载下一页").trigger("click");
    expect(button("刷新历史").element.disabled).toBe(true);
    await button("刷新历史").trigger("click");
    expect(count).toBe(2);
    expect(requests.at(-1).url).toContain("before_id=21");
    waiting.resolve(
      response({ items: [{ ...change, id: 20, site_name: "older-site" }] }),
    );
    await flushPromises();
    expect(wrapper.get('[aria-label="变更历史"]').text()).toContain(
      "older-site",
    );
    expect(wrapper.findAll("[data-change-id]")).toHaveLength(21);
  });
  it("disables an expired preview without sending execute", async () => {
    setup((url) =>
      url.endsWith("/preview")
        ? response({ ...preview, expires_at: "2000-01-01T00:00:00Z" })
        : undefined,
    );
    await flushPromises();
    await button("生效").trigger("click");
    await flushPromises();
    expect(button("确认执行变更").element.disabled).toBe(true);
    expect(wrapper.text()).toContain("已过期");
    await button("确认执行变更").trigger("click");
    expect(calls("/execute")).toHaveLength(0);
  });
});

it("reads backend timestamps without offsets as UTC", async () => {
  vi.spyOn(Date.prototype, "toLocaleString").mockImplementation(function () {
    return this.toISOString();
  });
  setup();
  await flushPromises();
  expect(wrapper.get('[aria-label="站点列表"]').text()).toContain(
    "2026-11-02T07:20:00.000Z",
  );
  expect(wrapper.get('[aria-label="变更历史"]').text()).toContain(
    "2026-11-02T07:20:00.000Z",
  );
  vi.restoreAllMocks();
});
it("waits for an in-flight history page, then refreshes history after execute finishes", async () => {
  const waiting = deferred();
  let count = 0;
  setup((url) => {
    if (!url.startsWith("/api/site-changes?")) return undefined;
    count++;
    if (count === 1)
      return response({
        items: Array.from({ length: 20 }, (_, i) => ({
          ...change,
          id: 40 - i,
        })),
      });
    if (count === 2) return waiting.promise;
    return response({
      items: [{ ...change, id: 50, site_name: "latest-execution" }],
    });
  });
  await flushPromises();
  await button("加载下一页").trigger("click");
  await button("删除").trigger("click");
  await flushPromises();
  await button("确认执行变更").trigger("click");
  await flushPromises();
  expect(count).toBe(2);
  waiting.resolve(response({ items: [{ ...change, id: 20 }] }));
  await flushPromises();
  expect(count).toBe(3);
  expect(wrapper.get('[aria-label="变更历史"]').text()).toContain(
    "latest-execution",
  );
  expect(wrapper.findAll("[data-change-id]")).toHaveLength(1);
});
it("preserves an actionable execute error if the following site-list refresh also fails", async () => {
  let count = 0;
  setup((url) => {
    if (url === "/api/sites" && ++count > 1)
      return response({ detail: "列表读取失败" }, 503);
    if (url.endsWith("/execute"))
      return response(
        {
          detail: {
            message: "请人工核查配置",
            change_id: 30,
            status: "manual_review_required",
          },
        },
        503,
      );
  });
  await flushPromises();
  await button("删除").trigger("click");
  await flushPromises();
  await button("确认执行变更").trigger("click");
  await flushPromises();
  expect(
    wrapper
      .findAll('[role="alert"]')
      .map((p) => p.text())
      .join(" "),
  ).toContain("请人工核查配置");
  expect(wrapper.text()).toContain("列表读取失败");
});
it("ignores a delayed initial site list after execution has refreshed the list", async () => {
  const waiting = deferred();
  let count = 0;
  setup((url) => {
    if (url === "/api/sites")
      return ++count === 1
        ? waiting.promise
        : response({ items: [{ ...site, name: "fresh-site" }] });
  });
  await createForm();
  await wrapper.get("form").trigger("submit");
  await flushPromises();
  await button("确认执行变更").trigger("click");
  await flushPromises();
  expect(wrapper.get('[aria-label="站点列表"]').text()).toContain("fresh-site");
  waiting.resolve(response({ items: [site] }));
  await flushPromises();
  expect(wrapper.get('[aria-label="站点列表"]').text()).toContain("fresh-site");
});

it.each([true, false])(
  "keeps loaded history pages after execute fails and preserves pagination (has more: %s)",
  async (morePages) => {
    let historyReads = 0;
    const oldestId = morePages ? 1 : 14;
    setup((url) => {
      if (url.endsWith("/execute")) {
        return response(
          {
            detail: {
              message: "需人工核查",
              change_id: 41,
              status: "manual_review_required",
            },
          },
          503,
        );
      }
      if (!url.startsWith("/api/site-changes?")) return undefined;
      historyReads++;
      if (historyReads === 1)
        return response({
          items: Array.from({ length: 20 }, (_, i) => ({
            ...change,
            id: 40 - i,
          })),
        });
      if (historyReads === 2)
        return response({
          items: Array.from({ length: morePages ? 20 : 7 }, (_, i) => ({
            ...change,
            id: 20 - i,
            site_name: "older-evidence",
          })),
        });
      if (historyReads === 3)
        return response({
          items: Array.from({ length: 20 }, (_, i) => ({
            ...change,
            id: 41 - i,
            status: "recovery_failed",
            message: "最新核查结果",
          })),
        });
      return response({ items: [] });
    });
    await flushPromises();
    await button("加载下一页").trigger("click");
    await flushPromises();
    // The user opened older evidence before trying another operation.
    await wrapper
      .get(`[data-change-id="${oldestId}"] summary`)
      .trigger("click");
    wrapper.get(`[data-change-id="${oldestId}"] details`).element.open = true;
    await button("删除").trigger("click");
    await flushPromises();
    await button("确认执行变更").trigger("click");
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toContain("需人工核查");
    expect(wrapper.get('[aria-label="变更历史"]').text()).toContain(
      "older-evidence",
    );
    expect(
      wrapper.get(`[data-change-id="${oldestId}"] details`).element.open,
    ).toBe(true);
    const ids = wrapper
      .findAll("[data-change-id]")
      .map((row) => Number(row.attributes("data-change-id")));
    expect(ids).toEqual(
      Array.from({ length: 42 - oldestId }, (_, i) => 41 - i),
    );
    expect(wrapper.get('[data-change-id="30"]').text()).toContain(
      "最新核查结果",
    );
    expect(wrapper.get('[data-change-id="30"]').text()).toContain("恢复失败");
    expect(calls("/execute")).toHaveLength(1);
    if (morePages) {
      await button("加载下一页").trigger("click");
      await flushPromises();
      expect(requests.at(-1).url).toBe(
        "/api/site-changes?limit=20&before_id=1",
      );
    } else {
      expect(
        wrapper.findAll("button").some((b) => b.text() === "加载下一页"),
      ).toBe(false);
    }
  },
);
