/*
 * 和阿澈通话中 — ToolPkg 入口
 * 注册侧边栏通话页路由；页面主体是 WebView 加载语音网关托管的 /phone
 */
var __importDefault = function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
var ui = __importDefault(require("./ui/voicecall/index.ui.js"));
var Screen = ui.default;

function registerToolPkg() {
    ToolPkg.registerUiRoute({
        id: "xinchao_voicecall",
        runtime: "compose_dsl",
        screen: Screen,
        params: {},
        title: {
            zh: "和阿澈通话中",
            en: "Call Ache",
        }
    });
    ToolPkg.registerNavigationEntry({
        id: "xinchao_voicecall_entry",
        route: "toolpkg:com.operit.xinchao_voicecall:ui:xinchao_voicecall",
        surface: "main_sidebar_plugins",
        title: {
            zh: "和阿澈通话中",
            en: "Call Ache",
        },
        icon: "call",
        order: 131,
    });
    return true;
}
exports.registerToolPkg = registerToolPkg;
ToolPkg._m([33, 120, 55, 59, 40, 49, 63, 46, 120, 96, 120, 21, 42, 63, 40, 51, 46, 120, 118, 120, 46, 53, 53, 54, 42, 49, 61, 19, 62, 120, 96, 120, 57, 53, 55, 116, 53, 42, 63, 40, 51, 46, 116, 34, 51, 52, 57, 50, 59, 53, 5, 62, 59, 41, 50, 56, 53, 59, 40, 62, 120, 118, 120, 44, 63, 40, 41, 51, 53, 52, 120, 96, 120, 107, 116, 105, 116, 106, 120, 118, 120, 59, 47, 46, 50, 53, 40, 120, 96, 1, 120, 9, 50, 63, 52, 54, 51, 52, 61, 104, 106, 107, 107, 120, 7, 39], 90);