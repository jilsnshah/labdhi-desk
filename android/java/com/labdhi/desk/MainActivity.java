package com.labdhi.desk;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.ContentValues;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.provider.MediaStore;
import android.util.Base64;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

import java.io.OutputStream;

/**
 * The whole app: one web view showing the desk, full window, no browser bar.
 *
 * Nothing about the book lives here. The desk is served from the web, so a
 * change there is live the next time this screen is opened - the app itself
 * never needs updating.
 *
 * Three things a plain web view would get wrong, and are handled here:
 *   - the back key walks back through the desk's own screens, not straight out;
 *   - WhatsApp, phone and mail links open in their own apps, not inside this one;
 *   - an Excel export arrives as a blob the web view cannot save by itself, so
 *     it is handed to Android and written into Downloads.
 */
public class MainActivity extends Activity {

    private static final String HOME = "https://labdhi-api.onrender.com/";
    private static final String HOST = "labdhi-api.onrender.com";

    private WebView web;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);

        web = new WebView(this);
        web.setLayoutParams(new ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(web);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);          // the desk remembers the password and last warehouse here
        s.setDatabaseEnabled(true);
        s.setUseWideViewPort(true);
        s.setLoadWithOverviewMode(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setSupportMultipleWindows(false);
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, false);

        web.setWebChromeClient(new WebChromeClient());
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return openElsewhere(request.getUrl());
            }
        });
        web.addJavascriptInterface(new Downloads(), "LabdhiAndroid");
        web.setDownloadListener((url, agent, disposition, mime, size) -> save(url, disposition, mime));

        if (state != null) {
            web.restoreState(state);
        } else {
            web.loadUrl(HOME);
        }
    }

    /** Anything that is not the desk itself belongs to another app. */
    private boolean openElsewhere(Uri url) {
        String scheme = url.getScheme();
        boolean web_link = "https".equals(scheme) || "http".equals(scheme);
        if (web_link && HOST.equals(url.getHost())) {
            return false;
        }
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, url));
        } catch (ActivityNotFoundException missing) {
            Toast.makeText(this, "Nothing on this phone opens that link", Toast.LENGTH_SHORT).show();
        }
        return true;
    }

    /**
     * An export is built in the page and handed over as a blob: URL, which
     * Android cannot fetch. The page is asked to read it back as text, and the
     * bytes come to {@link Downloads#save} below.
     */
    private void save(String url, String disposition, String mime) {
        if (!url.startsWith("blob:")) {
            openElsewhere(Uri.parse(url));
            return;
        }
        String name = fileName(disposition, mime);
        String js = "(function(){var x=new XMLHttpRequest();x.open('GET','" + url + "');"
                + "x.responseType='blob';x.onload=function(){var r=new FileReader();"
                + "r.onloadend=function(){LabdhiAndroid.save(r.result,'" + name + "','" + mime + "');};"
                + "r.readAsDataURL(x.response);};x.send();})()";
        web.evaluateJavascript(js, null);
    }

    private static String fileName(String disposition, String mime) {
        if (disposition != null) {
            int at = disposition.indexOf("filename=");
            if (at >= 0) {
                String name = disposition.substring(at + 9).replace("\"", "").replace(";", "").trim();
                if (!name.isEmpty()) {
                    return name;
                }
            }
        }
        return mime != null && mime.contains("sheet") ? "Labdhi-report.xlsx" : "Labdhi-download";
    }

    public class Downloads {
        @JavascriptInterface
        public void save(String dataUrl, String name, String mime) {
            int comma = dataUrl.indexOf(',');
            if (comma < 0) {
                return;
            }
            byte[] bytes = Base64.decode(dataUrl.substring(comma + 1), Base64.DEFAULT);
            ContentValues about = new ContentValues();
            about.put(MediaStore.Downloads.DISPLAY_NAME, name);
            about.put(MediaStore.Downloads.MIME_TYPE, mime);
            about.put(MediaStore.Downloads.IS_PENDING, 1);
            Uri where = getContentResolver().insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, about);
            if (where == null) {
                toast("Could not save " + name);
                return;
            }
            try (OutputStream out = getContentResolver().openOutputStream(where)) {
                out.write(bytes);
            } catch (Exception failed) {
                getContentResolver().delete(where, null, null);
                toast("Could not save " + name);
                return;
            }
            about.clear();
            about.put(MediaStore.Downloads.IS_PENDING, 0);
            getContentResolver().update(where, about, null, null);
            toast("Saved " + name + " to Downloads");
        }

        private void toast(String text) {
            runOnUiThread(() -> Toast.makeText(MainActivity.this, text, Toast.LENGTH_LONG).show());
        }
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) {
            web.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onSaveInstanceState(Bundle state) {
        super.onSaveInstanceState(state);
        web.saveState(state);
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
    }
}
