/**
 * server.js —— WhatsApp Web 桥接服务（放置路径：whatsapp_bridge/server.js）
 *
 * 使用 whatsapp-web.js（基于 Puppeteer 控制 WhatsApp Web/Business 网页版）
 * 提供本地 HTTP 接口，供 Python GUI 调用，替代原来 Python 直接用 Selenium
 * 操作浏览器的方式。
 *
 * 会话持久化：使用 LocalAuth，session 数据保存在 SESSION_PATH（由 Python
 * 通过环境变量传入，指向应用数据目录下的 whatsapp_bridge_session 文件夹），
 * 效果等同于原来的“持久化 Chrome Profile”，首次扫码后无需重复扫码。
 *
 * 重要声明：本工具通过非官方方式自动化 WhatsApp Web/Business 网页版，
 * 不隶属于、不代表 WhatsApp/Meta 官方。部署前请自行确认符合贵组织政策
 * 及 WhatsApp 的适用条款；生产环境的合规消息发送建议评估官方
 * WhatsApp Business Platform（Cloud API）。
 *
 * 启动方式（由 Python 子进程调用，也可手动调试）：
 *   PORT=8765 SESSION_PATH="C:\path\to\session" node server.js
 */
const express = require("express");
const qrcode = require("qrcode");
const { Client, LocalAuth } = require("whatsapp-web.js");
const showBrowser = process.env.WHATSAPP_SHOW_BROWSER === 'true';
const headlessMode = !showBrowser;
const PORT = process.env.PORT || 8765;
const SESSION_PATH = process.env.SESSION_PATH || "./.wwebjs_auth";
const HEADLESS = process.env.HEADLESS !== "false"; // 默认无头，可通过环境变量关闭便于调试

const app = express();
app.use(express.json({ limit: "10mb" }));

let latestQrDataUrl = null;

let clientStatus = "STARTING";
// STARTING | QR_PENDING | AUTHENTICATED | CONNECTED | READY |
// AUTH_FAILURE | DISCONNECTED | ERROR

let statusDetail = "正在初始化 WhatsApp Client";
let lastClientState = "UNKNOWN";
let lastStatusAt = new Date().toISOString();

function setBridgeStatus(status, detail = "") {
  clientStatus = status;
  statusDetail = detail;
  lastStatusAt = new Date().toISOString();

  console.log(
    `[bridge] 状态更新：status=${clientStatus}，detail=${statusDetail}`,
  );
}

const client = new Client({
  authStrategy: new LocalAuth({ dataPath: SESSION_PATH }),
  puppeteer: {
    headless: HEADLESS,
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  },
});
console.log(
    `[WhatsApp] Run mode: ${showBrowser ? 'visible browser window' : 'background / headless'}`
);
async function getWhatsAppPageCapability() {
  if (!client.pupPage) {
    return {
      usable: false,
      reason: "Puppeteer 页面尚未建立",
    };
  }

  try {
    return await client.pupPage.evaluate(() => {
      const hasWWebJS = typeof window.WWebJS !== "undefined";

      const hasWWebJSGetChats =
        typeof window.WWebJS?.getChats === "function";

      const hasRequire =
        typeof window.require === "function";

      let hasRawChatCollection = false;
      let rawChatCount = 0;
      let collectionError = "";

      try {
        if (hasRequire) {
          const collections = window.require("WAWebCollections");
          const chatCollection = collections?.Chat;

          hasRawChatCollection =
            typeof chatCollection?.getModelsArray === "function";

          if (hasRawChatCollection) {
            rawChatCount = chatCollection.getModelsArray().length;
          }
        }
      } catch (error) {
        collectionError = String(error?.message || error);
      }

      return {
        usable:
          hasWWebJS &&
          hasWWebJSGetChats &&
          hasRawChatCollection,

        url: location.href,
        title: document.title,
        readyState: document.readyState,

        hasWWebJS,
        hasWWebJSGetChats,
        hasRequire,
        hasRawChatCollection,
        rawChatCount,
        collectionError,

        bodyText:
          document.body?.innerText?.slice(0, 200) || "",
      };
    });
  } catch (error) {
    return {
      usable: false,
      reason: String(error?.message || error),
    };
  }
}
client.on("qr", async (qr) => {
  try {
    latestQrDataUrl = await qrcode.toDataURL(qr);

    setBridgeStatus(
      "QR_PENDING",
      "等待使用手机 WhatsApp 或 WhatsApp Business 扫描二维码",
    );

    console.log("[bridge] 已产生新的登入二维码");
  } catch (error) {
    latestQrDataUrl = null;

    setBridgeStatus(
      "ERROR",
      `产生二维码失败：${String(error?.message || error)}`,
    );

    console.error("[bridge] 产生二维码失败：", error);
  }
});

client.on("authenticated", () => {
  latestQrDataUrl = null;

  setBridgeStatus(
    "AUTHENTICATED",
    "WhatsApp 已认证，正在恢复会话及等待客户端完成初始化",
  );

  console.log("[bridge] WhatsApp 已认证，正在等待 ready event");

  /*
   * whatsapp-web.js 正常情况下会继续触发 ready。
   * 但部分 WhatsApp Web / whatsapp-web.js 版本组合会出现：
   * 页面、聊天及 WWebJS 接口都已可用，但 ready event 没有回调。
   *
   * 因此延迟 12 秒后检查真实页面能力；
   * 仅在现有群组读取所需接口均可用时，才安全补设 READY。
   */
  setTimeout(async () => {
    if (clientStatus === "READY") {
      return;
    }

    if (
      clientStatus === "AUTH_FAILURE" ||
      clientStatus === "DISCONNECTED" ||
      clientStatus === "ERROR"
    ) {
      return;
    }

    const capability = await getWhatsAppPageCapability();

    console.log(
      "[bridge] 认证后页面能力检查：",
      JSON.stringify(capability),
    );

    if (capability.usable) {
      setBridgeStatus(
        "READY",
        `WhatsApp 页面已可用（fallback）：已载入 ${capability.rawChatCount} 个聊天`,
      );

      console.warn(
        "[bridge] 未收到 ready event，但聊天接口已可用；已安全设为 READY",
      );

      return;
    }

    setBridgeStatus(
      "AUTHENTICATED",
      `认证已完成，但聊天接口尚未可用：${capability.reason || capability.collectionError || "正在等待页面同步"}`,
    );

    console.warn(
      "[bridge] 认证后页面尚未可用，保持 AUTHENTICATED：",
      capability,
    );
  }, 12_000);
});

client.on("ready", () => {
  latestQrDataUrl = null;

  setBridgeStatus(
    "READY",
    "WhatsApp Client 已就绪，可读取群组及传送消息",
  );

  console.log("[bridge] WhatsApp Client 已进入 READY 状态");
});

client.on("change_state", (state) => {
  lastClientState = String(state || "UNKNOWN");

  console.log(`[bridge] WhatsApp 内部状态变更：${lastClientState}`);

  /*
   * CONNECTED 表示 WhatsApp Web 连接已建立。
   * 正常情况下稍后仍应由 ready event 把状态设为 READY。
   * 这里只记录 CONNECTED，不强行允许发送或读取群组。
   */
  if (lastClientState === "CONNECTED" && clientStatus !== "READY") {
    setBridgeStatus(
      "CONNECTED",
      "WhatsApp 已连接，正在等待客户端完全就绪",
    );
  }
});

client.on("auth_failure", (message) => {
  latestQrDataUrl = null;

  setBridgeStatus(
    "AUTH_FAILURE",
    `认证失败：${String(message || "未知原因")}`,
  );

  console.error("[bridge] WhatsApp 认证失败：", message);
});

client.on("disconnected", (reason) => {
  latestQrDataUrl = null;

  setBridgeStatus(
    "DISCONNECTED",
    `WhatsApp 已断线：${String(reason || "未知原因")}`,
  );

  console.error("[bridge] WhatsApp 已断线：", reason);
});

console.log("[bridge] 开始初始化 WhatsApp Client");

client.initialize().catch((error) => {
  setBridgeStatus(
    "ERROR",
    `WhatsApp Client 初始化失败：${String(error?.message || error)}`,
  );

  console.error("[bridge] WhatsApp Client 初始化失败：", error);
  console.error(error?.stack || "(没有 stack)");
});

// ---- HTTP 接口 ----

app.get("/status", (req, res) => {
  res.json({
    status: clientStatus,
    detail: statusDetail,
    last_client_state: lastClientState,
    has_qr: Boolean(latestQrDataUrl),
    updated_at: lastStatusAt,
    headless: HEADLESS,
    session_path: SESSION_PATH,
  });
});
app.get("/qr", async (req, res) => {
  if (!latestQrDataUrl) {
    return res.status(404).json({ error: "当前没有待扫描的二维码（可能已登录或尚未生成）" });
  }
  res.json({ qr_data_url: latestQrDataUrl });
});

/** 列出所有群组聊天的名称，供 Python 端做“测试定位群组”对照 */
app.get("/groups", async (req, res) => {
  if (clientStatus !== "READY") {
    return res.status(409).json({ error: "WhatsApp 尚未登录/就绪" });
  }
  try {
    const chats = await client.getChats();
    const groups = chats.filter((c) => c.isGroup).map((c) => ({ name: c.name, id: c.id._serialized }));
    res.json({ groups });
  } catch (err) {
    res.status(500).json({ error: String(err) });
  }
});

/** 按精确名称查找群组是否存在 */
// ============================================================
// GET /verify_group?name=群组精确名称
//
// 使用与 /list_groups 相同的原始聊天集合进行精确匹配。
// 避免 client.getChats() / getChatModel() 的 IndexedDB 异常。
// ============================================================
app.get('/verify_group', async (req, res) => {
    try {
        const targetName = String(req.query.name || '').trim();

        if (!targetName) {
            return res.status(400).json({
                found: false,
                error: '缺少群组名称参数 name。',
            });
        }

        if (!client.pupPage) {
            return res.status(503).json({
                found: false,
                error: 'WhatsApp 页面尚未启动，请先登录。',
            });
        }

        const result = await client.pupPage.evaluate((targetName) => {
            if (typeof window.require !== 'function') {
                throw new Error(
                    'window.require 不可用，WhatsApp Web 尚未完全加载。',
                );
            }

            const ChatCollection =
                window.require('WAWebCollections').Chat;

            const rawChats = ChatCollection.getModelsArray();

            for (const chat of rawChats) {
                const rawId = chat?.id;

                const id = rawId?._serialized ||
                    (
                        rawId?.user && rawId?.server
                            ? `${rawId.user}@${rawId.server}`
                            : ''
                    );

                const server =
                    rawId?.server ||
                    id.split('@').pop() ||
                    '';

                const isGroup =
                    chat?.isGroup === true ||
                    server === 'g.us' ||
                    id.endsWith('@g.us');

                if (!isGroup) {
                    continue;
                }

                const name = (
                    chat?.name ||
                    chat?.formattedTitle ||
                    chat?.contact?.name ||
                    ''
                ).trim();

                if (name === targetName) {
                    return {
                        found: true,
                        id,
                        name,
                    };
                }
            }

            return {
                found: false,
            };
        }, targetName);

        console.log(
            `[verify_group] name="${targetName}"，found=${result.found}`,
        );

        return res.json(result);
    } catch (error) {
        console.error('[verify_group] 验证群组失败：', error);

        return res.status(500).json({
            found: false,
            error: String(error?.message || error),
        });
    }
});
// 搜索群组：GET /search_groups?keyword=xxx（keyword 为空则返回全部群组）
app.get('/search_groups', async (req, res) => {
  try {
    const keyword = (req.query.keyword || '').toString().trim().toLowerCase();
    const chats = await client.getChats();
    const groups = chats
      .filter(chat => chat.isGroup && (!keyword || chat.name.toLowerCase().includes(keyword)))
      .map(chat => ({ id: chat.id._serialized, name: chat.name }));
    res.json({ groups });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});
// 获取当前账号下全部群组：GET /list_groups
// 获取全部 WhatsApp 群组。
// 特意不调用 client.getChats()：该方法在部分新版 WhatsApp Web 数据结构上
// 会在 whatsapp-web.js 的 getChatModel 序列化阶段抛出 "r"。
// GET /list_groups
// 列出当前账号已同步的 WhatsApp 群组及其 Group ID（JID）。
//
// 重点：
// 1. 不调用 client.getChats()，它会把整批聊天包装成 Chat 类，
//    某一条异常聊天会导致整个调用失败并返回压缩错误 "r"。
// 2. 不使用 window.Store，它不是 whatsapp-web.js 稳定公开接口。
// 3. 使用当前已确认存在的 window.WWebJS。
// ============================================================
// 获取 WhatsApp 群组列表
//
// 使用方法：
// 1. 获取全部群组：
//    http://127.0.0.1:8765/list_groups
//
// 2. 按名称关键词粗略搜索：
//    http://127.0.0.1:8765/list_groups?keyword=工程
//
// 返回格式：
// {
//   "groups": [
//     {
//       "id": "120363xxxxxxxxxxxx@g.us",
//       "name": "工程告警群"
//     }
//   ],
//   "count": 1,
//   "keyword": "工程"
// }
// ============================================================
// ============================================================
// GET /list_groups
//
// 用途：
// - 不带 keyword：返回全部已同步群组
// - 带 keyword：按群组名称进行粗略搜索
//
// 示例：
// http://127.0.0.1:8765/list_groups
// http://127.0.0.1:8765/list_groups?keyword=工程
// ============================================================
// ============================================================
// GET /list_groups
//
// 用途：
// 1. 不输入关键词：返回全部已同步的 WhatsApp 群组
// 2. 输入关键词：按群组名称粗略搜索
//
// 示例：
// http://127.0.0.1:8765/list_groups
// http://127.0.0.1:8765/list_groups?keyword=工程
//
// 设计原则：
// - 不调用 client.getChats()
// - 不调用 window.WWebJS.getChats()
// - 不调用 getChatModel()
// - 直接读取 WhatsApp Web 原始聊天集合，只取名称和 ID
// ============================================================
app.get('/list_groups', async (req, res) => {
    try {
        const keyword = String(req.query.keyword || '')
            .trim()
            .toLowerCase();

        if (!client.pupPage) {
            return res.status(503).json({
                error: 'WhatsApp 页面尚未启动，请先点击“登录 WhatsApp Web”。',
            });
        }

        const result = await client.pupPage.evaluate((keyword) => {
            // 这个 require 是 whatsapp-web.js 注入到 WhatsApp Web 页面的模块加载器。
            if (typeof window.require !== 'function') {
                throw new Error(
                    'window.require 不可用。请确认 WhatsApp Web 已完全加载。',
                );
            }

            const ChatCollection = window.require('WAWebCollections').Chat;

            if (
                !ChatCollection ||
                typeof ChatCollection.getModelsArray !== 'function'
            ) {
                throw new Error(
                    '无法取得 WAWebCollections.Chat。WhatsApp Web 内部模块可能尚未加载完成。',
                );
            }

            // 直接取得 WhatsApp Web 的原始对话对象。
            // 不调用 getChatModel，因此不会读取容易出错的 IndexedDB 附加数据。
            const rawChats = ChatCollection.getModelsArray();

            const groups = [];
            const skipped = [];

            for (let index = 0; index < rawChats.length; index += 1) {
                try {
                    const chat = rawChats[index];
                    const rawId = chat?.id;

                    // 群组 ID 正常形式：
                    // 120363012345678901@g.us
                    const id = rawId?._serialized ||
                        (
                            rawId?.user && rawId?.server
                                ? `${rawId.user}@${rawId.server}`
                                : ''
                        );

                    if (!id) {
                        skipped.push({
                            index,
                            reason: '缺少 Chat ID',
                        });
                        continue;
                    }

                    const server =
                        rawId?.server ||
                        id.split('@').pop() ||
                        '';

                    const isGroup =
                        chat?.isGroup === true ||
                        server === 'g.us' ||
                        id.endsWith('@g.us');

                    // 这里只显示群组，不显示个人对话。
                    if (!isGroup || !id.endsWith('@g.us')) {
                        continue;
                    }

                    const name = (
                        chat?.name ||
                        chat?.formattedTitle ||
                        chat?.contact?.name ||
                        '(未命名群组)'
                    ).trim();

                    // keyword 为空：显示全部群组。
                    // keyword 非空：群名包含关键字才显示。
                    if (
                        keyword &&
                        !name.toLowerCase().includes(keyword)
                    ) {
                        continue;
                    }

                    groups.push({
                        id,
                        name,
                    });
                } catch (itemError) {
                    skipped.push({
                        index,
                        reason: String(itemError?.message || itemError),
                    });
                }
            }

            groups.sort((a, b) =>
                a.name.localeCompare(b.name, 'zh-Hans-CN'),
            );

            return {
                total_raw_chats: rawChats.length,
                groups,
                skipped,
            };
        }, keyword);

        console.log(
            `[list_groups] 原始对话=${result.total_raw_chats}，` +
            `群组=${result.groups.length}，` +
            `跳过=${result.skipped.length}，` +
            `keyword="${keyword}"`,
        );

        return res.json({
            groups: result.groups,
            count: result.groups.length,
            keyword,
            total_raw_chats: result.total_raw_chats,
            skipped_count: result.skipped.length,
            skipped: result.skipped,
        });
    } catch (error) {
        console.error('[list_groups] 获取群组列表失败：');
        console.error(error);
        console.error(error?.stack || '(没有 stack)');

        return res.status(500).json({
            error: String(error?.message || error),
            stack: error?.stack || '',
        });
    }
});
app.get('/debug_whatsapp', async (req, res) => {
  try {
    const pageInfo = await client.pupPage.evaluate(() => ({
      url: location.href,
      title: document.title,
      hasStore: typeof window.Store !== 'undefined',
      hasStoreChat: typeof window.Store?.Chat !== 'undefined',
      hasStoreChatModels:
        typeof window.Store?.Chat?.getModelsArray === 'function',

      hasWWebJS: typeof window.WWebJS !== 'undefined',
      hasWWebJSGetChats:
        typeof window.WWebJS?.getChats === 'function',

      bodyText: document.body?.innerText?.slice(0, 300) || '',
    }));

    console.log('[debug_whatsapp]', pageInfo);
    return res.json(pageInfo);
  } catch (err) {
    console.error('[debug_whatsapp] 失败：', err);
    return res.status(500).json({
      error: String(err?.message || err),
      stack: err?.stack || '',
    });
  }
});
/** 发送文字 + 可选图片到指定群组（按精确名称匹配） */
app.post("/send", async (req, res) => {
  const { group_name, text, image_path } = req.body || {};

  // ----------------------------------------------------------
  // 1. 基础状态和参数检查
  // ----------------------------------------------------------
  if (clientStatus !== "READY") {
    return res.status(409).json({
      status: "FAILED",
      detail: "WhatsApp 尚未登录/就绪",
    });
  }

  if (!group_name || !String(group_name).trim() || !text) {
    return res.status(400).json({
      status: "FAILED",
      detail: "缺少 group_name 或 text 参数",
    });
  }

  const targetGroupName = String(group_name).trim();

  try {
    // ----------------------------------------------------------
    // 2. 使用与 /list_groups、/verify_group 一致的方式
    //    从 WhatsApp Web 原始聊天集合中定位群组。
    //
    //    不使用 client.getChats()：
    //    它会触发 getChatModel / IndexedDB 异常，导致明明存在
    //    的 Test 群组在发送阶段却找不到。
    // ----------------------------------------------------------
    if (!client.pupPage) {
      return res.status(503).json({
        status: "FAILED",
        detail: "WhatsApp 页面尚未启动，请重新登录 WhatsApp Web",
      });
    }

    const targetGroup = await client.pupPage.evaluate(
      (targetGroupName) => {
        if (typeof window.require !== "function") {
          throw new Error(
            "window.require 不可用；WhatsApp Web 尚未完全加载。",
          );
        }

        const ChatCollection =
          window.require("WAWebCollections").Chat;

        if (
          !ChatCollection ||
          typeof ChatCollection.getModelsArray !== "function"
        ) {
          throw new Error(
            "WAWebCollections.Chat 不可用；请等待 WhatsApp Web 完成同步。",
          );
        }

        const rawChats = ChatCollection.getModelsArray();

        for (const chat of rawChats) {
          try {
            const rawId = chat?.id;

            // 群组 JID 标准形式，例如：
            // 120363430652857181@g.us
            const id = rawId?._serialized ||
              (
                rawId?.user && rawId?.server
                  ? `${rawId.user}@${rawId.server}`
                  : ""
              );

            if (!id) {
              continue;
            }

            const server =
              rawId?.server ||
              id.split("@").pop() ||
              "";

            const isGroup =
              chat?.isGroup === true ||
              server === "g.us" ||
              id.endsWith("@g.us");

            // 只接受群组，排除个人聊天和系统聊天。
            if (!isGroup || !id.endsWith("@g.us")) {
              continue;
            }

            const name = (
              chat?.name ||
              chat?.formattedTitle ||
              chat?.contact?.name ||
              ""
            ).trim();

            // 这里维持现有设计：群组名称必须精确匹配。
            if (name === targetGroupName) {
              return {
                id,
                name,
              };
            }
          } catch (itemError) {
            // 单个聊天异常不影响继续查找其他群组。
            console.warn(
              "[bridge/send] 跳过异常聊天：",
              String(itemError?.message || itemError),
            );
          }
        }

        return null;
      },
      targetGroupName,
    );

    // ----------------------------------------------------------
    // 3. 找不到群组时，维持 Python 端目前能识别的 FAILED 格式
    // ----------------------------------------------------------
    if (!targetGroup) {
      console.warn(
        `[bridge/send] 未找到名称完全匹配的群组：${targetGroupName}`,
      );

      return res.json({
        status: "FAILED",
        detail: `未找到名称完全匹配的群组：${targetGroupName}`,
      });
    }

    console.log(
      `[bridge/send] 已定位群组：name="${targetGroup.name}"，id="${targetGroup.id}"`,
    );

    // ----------------------------------------------------------
    // 4. 使用真实 Group ID（JID）发送文字
    // ----------------------------------------------------------
    await client.sendMessage(targetGroup.id, text);

    // ----------------------------------------------------------
    // 5. 如有图片，继续发送图片；图片失败不会影响已成功的文字
    // ----------------------------------------------------------
    if (image_path) {
      try {
        const { MessageMedia } = require("whatsapp-web.js");

        const media = MessageMedia.fromFilePath(image_path);

        await client.sendMessage(targetGroup.id, media);
      } catch (imgErr) {
        console.error(
          "[bridge/send] 图片发送失败，已降级为仅发送文字：",
          imgErr,
        );

        return res.json({
          status: "SENT_TEXT_ONLY",
          detail: String(imgErr?.message || imgErr),
          group_id: targetGroup.id,
        });
      }
    }

    // ----------------------------------------------------------
    // 6. 完整成功
    // ----------------------------------------------------------
    return res.json({
      status: "SENT",
      detail: "",
      group_id: targetGroup.id,
    });
  } catch (err) {
    console.error("[bridge/send] 发送失败：", err);
    console.error(err?.stack || "(没有 stack)");

    return res.json({
      status: "FAILED",
      detail: String(err?.message || err),
    });
  }
});

/** 优雅关闭（供 Python 退出时调用） */
app.post("/shutdown", async (req, res) => {
  res.json({ ok: true });
  try {
    await client.destroy();
  } finally {
    process.exit(0);
  }
});

app.listen(PORT, "127.0.0.1", () => {
  console.log(`[bridge] WhatsApp 桥接服务已启动，监听 http://127.0.0.1:${PORT}`);
});
