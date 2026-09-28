package app.augrammepres.recettes;

import android.os.Bundle;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {

    @Override
    public void onCreate(Bundle savedInstanceState) {
        // Reçoit les recettes partagées depuis Instagram, TikTok, YouTube, la galerie…
        registerPlugin(ShareInPlugin.class);
        // Navigateur caché : lit une publication comme Chrome (avec ton compte si tu t'es connecté)
        registerPlugin(WebReaderPlugin.class);
        super.onCreate(savedInstanceState);
    }
}
