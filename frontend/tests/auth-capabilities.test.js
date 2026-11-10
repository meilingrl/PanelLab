import { afterEach, expect, it, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import Login from "../src/views/Login.vue";
import Register from "../src/views/Register.vue";
vi.mock("vue-router", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useRoute: () => ({ query: {} }),
}));
vi.mock("../src/theme", () => ({
  initTheme: vi.fn(),
  getTheme: () => "light",
  toggleTheme: () => "dark",
}));
const capabilities = {
  password: true,
  register: false,
  sms: false,
  wechat: false,
  qq: false,
  demo: false,
};
let wrapper;
afterEach(() => {
  wrapper?.unmount();
  vi.unstubAllGlobals();
});
const mountPage = (component) =>
  mount(component, {
    global: {
      stubs: { WavyLines: true, RouterLink: { template: "<a><slot /></a>" } },
    },
  });
it("hides disabled login methods and registration when production capabilities arrive", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: true, json: async () => capabilities })),
  );
  wrapper = mountPage(Login);
  await flushPromises();
  expect(wrapper.text()).toContain("账号密码");
  for (const closed of ["手机验证码", "微信", "QQ", "注册", "模拟"])
    expect(wrapper.text()).not.toContain(closed);
  expect(wrapper.findAll("form")).toHaveLength(1);
});
it("does not flash a registration form while capabilities are loading, then shows closed state on the direct route", async () => {
  let resolve;
  vi.stubGlobal(
    "fetch",
    vi.fn(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    ),
  );
  wrapper = mountPage(Register);
  expect(wrapper.find("form").exists()).toBe(false);
  resolve({ ok: true, json: async () => capabilities });
  await flushPromises();
  expect(wrapper.find("form").exists()).toBe(false);
  expect(wrapper.text()).toContain("注册已关闭");
});
it("fails closed on a capabilities network failure with a readable error", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      throw new Error("offline");
    }),
  );
  wrapper = mountPage(Register);
  await flushPromises();
  expect(wrapper.find("form").exists()).toBe(false);
  expect(wrapper.text()).toContain("登录能力读取失败");
});
it("shows the registration form only after the capability is explicitly enabled", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      json: async () => ({ ...capabilities, register: true }),
    })),
  );
  wrapper = mountPage(Register);
  expect(wrapper.find("form").exists()).toBe(false);
  await flushPromises();
  expect(wrapper.find("form").exists()).toBe(true);
});
