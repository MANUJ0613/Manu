package app.augrammepres.recettes;

import android.annotation.SuppressLint;
import android.app.Dialog;
import android.graphics.Color;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.ViewGroup;
import android.view.Window;
import android.webkit.CookieManager;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;
import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import org.json.JSONObject;
import org.json.JSONTokener;

/**
 * Un vrai navigateur caché dans l'appli : ouvre une publication comme Chrome (avec le compte
 * Facebook ou Instagram de l'utilisateur s'il s'est connecté) et renvoie le texte affiché.
 *
 * read({url, wait, timeout})  → { url, title, text, ogTitle, ogDesc, ogImage, image, video }
 * open({url, title})          → fenêtre plein écran pour se connecter, { cookies } à la fermeture
 * cookies({url})              → { cookies }
 * logout({urls})              → supprime les cookies de ces sites
 */
@CapacitorPlugin(name = "WebReader")
public class WebReaderPlugin extends Plugin {

    private static final String EXTRACT_JS =
        "(function(){try{var d=document;"
            + "function m(p){var e=d.querySelector('meta[property=\"'+p+'\"],meta[name=\"'+p+'\"]');return e?(e.getAttribute('content')||''):'';}"
            + "var vs='';var vids=d.querySelectorAll('video');for(var i=0;i<vids.length;i++){var s=vids[i].currentSrc||vids[i].src||'';"
            + "if(!s){var so=vids[i].querySelector('source');s=so?so.src:'';}if(s&&s.indexOf('blob:')!==0){vs=s;break;}}"
            + "var best='',area=0;var im=d.images||[];for(var k=0;k<im.length;k++){var x=im[k];var a=(x.naturalWidth||0)*(x.naturalHeight||0);"
            + "if(x.naturalWidth>=300&&/scontent|cdninstagram|fbcdn/.test(x.src)&&!/static\\.|rsrc/.test(x.src)&&a>area){area=a;best=x.src;}}"
            + "return JSON.stringify({url:location.href,title:d.title||'',text:(d.body?d.body.innerText:'').slice(0,30000),"
            + "ogTitle:m('og:title'),ogDesc:m('og:description'),ogImage:m('og:image'),video:vs,image:best});"
            + "}catch(e){return JSON.stringify({error:String(e)});}})()";

    private final Handler main = new Handler(Looper.getMainLooper());

    private static String cleanUa(String ua) {
        if (ua == null) return null;
        return ua.replace("; wv)", ")").replaceAll("Version/\\d+\\.\\d+ ", "");
    }

    private static boolean isWeb(Uri u) {
        String sc = u == null ? null : u.getScheme();
        return "http".equals(sc) || "https".equals(sc);
    }

    private int dp(int v) {
        return Math.round(v * getContext().getResources().getDisplayMetrics().density);
    }

    private int statusBarHeight() {
        int id = getContext().getResources().getIdentifier("status_bar_height", "dimen", "android");
        return id > 0 ? getContext().getResources().getDimensionPixelSize(id) : 0;
    }

    @SuppressLint("SetJavaScriptEnabled")
    private WebView makeWebView() {
        WebView wv = new WebView(getContext());
        WebSettings s = wv.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(true);
        s.setUserAgentString(cleanUa(s.getUserAgentString()));
        CookieManager cm = CookieManager.getInstance();
        cm.setAcceptCookie(true);
        cm.setAcceptThirdPartyCookies(wv, true);
        return wv;
    }

    private void cleanup(WebView wv) {
        main.removeCallbacksAndMessages(wv);
        try {
            ViewGroup p = (ViewGroup) wv.getParent();
            if (p != null) p.removeView(wv);
            wv.stopLoading();
            wv.destroy();
        } catch (Exception ignored) {
            // déjà détruit
        }
        CookieManager.getInstance().flush();
    }

    @PluginMethod
    public void read(final PluginCall call) {
        final String url = call.getString("url", "");
        final int wait = call.getInt("wait", 3500);
        final int timeout = call.getInt("timeout", 25000);
        if (url == null || url.isEmpty()) {
            call.reject("url manquante");
            return;
        }
        getActivity().runOnUiThread(() -> {
            final WebView wv;
            try {
                wv = makeWebView();
            } catch (Exception e) {
                call.reject("Navigateur indisponible", e);
                return;
            }
            final boolean[] finished = { false };
            final Runnable extract = new Runnable() {
                @Override
                public void run() {
                    if (finished[0]) return;
                    finished[0] = true;
                    wv.evaluateJavascript(EXTRACT_JS, value -> {
                        JSObject out;
                        try {
                            Object o = new JSONTokener(value).nextValue();
                            String json = o instanceof String ? (String) o : String.valueOf(o);
                            out = JSObject.fromJSONObject(new JSONObject(json));
                        } catch (Exception e) {
                            out = new JSObject();
                            out.put("error", "lecture impossible");
                        }
                        cleanup(wv);
                        call.resolve(out);
                    });
                }
            };
            wv.setWebViewClient(new WebViewClient() {
                @Override
                public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                    // bloque les ouvertures d'appli (fb://, intent://)
                    return !isWeb(request.getUrl());
                }

                @Override
                public void onPageFinished(WebView view, String u) {
                    main.removeCallbacksAndMessages(view);
                    main.postAtTime(extract, view, SystemClock.uptimeMillis() + wait);
                }
            });
            ViewGroup root = getActivity().findViewById(android.R.id.content);
            // derrière la page de l'appli : il s'affiche vraiment (le code de Facebook tourne) mais reste caché
            root.addView(wv, 0, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
            main.postDelayed(extract, timeout);
            wv.loadUrl(url);
        });
    }

    @PluginMethod
    public void open(final PluginCall call) {
        final String url = call.getString("url", "https://m.facebook.com/login/");
        final String title = call.getString("title", "Connexion");
        getActivity().runOnUiThread(() -> {
            final Dialog dialog = new Dialog(getActivity(), android.R.style.Theme_DeviceDefault_Light_NoActionBar);
            LinearLayout col = new LinearLayout(getContext());
            col.setOrientation(LinearLayout.VERTICAL);
            col.setBackgroundColor(Color.WHITE);

            LinearLayout bar = new LinearLayout(getContext());
            bar.setOrientation(LinearLayout.HORIZONTAL);
            bar.setGravity(Gravity.CENTER_VERTICAL);
            bar.setBackgroundColor(Color.rgb(247, 244, 238));
            bar.setPadding(dp(16), dp(8) + statusBarHeight(), dp(8), dp(8));
            TextView t = new TextView(getContext());
            t.setText(title);
            t.setTextSize(17);
            t.setTypeface(Typeface.DEFAULT_BOLD);
            t.setTextColor(Color.rgb(28, 26, 23));
            bar.addView(t, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
            Button done = new Button(getContext());
            done.setText("Terminé");
            done.setAllCaps(false);
            bar.addView(done, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT));
            col.addView(bar, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

            final WebView wv = makeWebView();
            wv.setWebViewClient(new WebViewClient() {
                @Override
                public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                    return !isWeb(request.getUrl());
                }
            });
            col.addView(wv, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
            dialog.setContentView(col);
            Window w = dialog.getWindow();
            if (w != null) w.setLayout(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT);

            done.setOnClickListener(v -> dialog.dismiss());
            dialog.setOnKeyListener((d, keyCode, event) -> {
                if (keyCode != KeyEvent.KEYCODE_BACK) return false;
                if (event.getAction() == KeyEvent.ACTION_UP) {
                    if (wv.canGoBack()) wv.goBack();
                    else dialog.dismiss();
                }
                return true;
            });
            dialog.setOnDismissListener(d -> {
                CookieManager cm = CookieManager.getInstance();
                cm.flush();
                JSObject r = new JSObject();
                String c = cm.getCookie(url);
                r.put("cookies", c == null ? "" : c);
                try {
                    wv.destroy();
                } catch (Exception ignored) {
                    // déjà détruit
                }
                call.resolve(r);
            });
            dialog.show();
            wv.loadUrl(url);
        });
    }

    @PluginMethod
    public void cookies(PluginCall call) {
        String url = call.getString("url", "");
        String c = CookieManager.getInstance().getCookie(url);
        JSObject r = new JSObject();
        r.put("cookies", c == null ? "" : c);
        call.resolve(r);
    }

    @PluginMethod
    public void logout(PluginCall call) {
        JSArray urls = call.getArray("urls");
        CookieManager cm = CookieManager.getInstance();
        if (urls != null) {
            for (int i = 0; i < urls.length(); i++) {
                try {
                    String u = urls.getString(i);
                    String c = cm.getCookie(u);
                    String host = Uri.parse(u).getHost();
                    if (c == null || host == null) continue;
                    String[] labels = host.split("\\.");
                    String domain = labels.length >= 2 ? "." + labels[labels.length - 2] + "." + labels[labels.length - 1] : host;
                    for (String part : c.split(";")) {
                        String name = part.split("=")[0].trim();
                        if (name.isEmpty()) continue;
                        cm.setCookie(u, name + "=; Max-Age=0; Path=/; Domain=" + domain);
                        cm.setCookie(u, name + "=; Max-Age=0; Path=/");
                    }
                } catch (Exception ignored) {
                    // cookie illisible : on continue
                }
            }
        }
        cm.flush();
        call.resolve();
    }
}
