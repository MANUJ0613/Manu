package app.augrammepres.recettes;

import android.content.ClipData;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.ImageDecoder;
import android.net.Uri;
import android.os.Build;
import android.util.Base64;
import androidx.core.content.IntentCompat;
import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.List;

/**
 * Reçoit ce que l'utilisateur partage vers l'appli (bouton « Partager » d'Instagram,
 * TikTok, YouTube, du navigateur ou de la galerie) et le transmet à la page web.
 *
 * Événement JS : "shared" → { id, type, text, subject, images: ["data:image/jpeg;base64,…"] }
 */
@CapacitorPlugin(name = "ShareIn")
public class ShareInPlugin extends Plugin {

    private static final int MAX_IMAGES = 6;
    private static final int MAX_SIDE = 1600;

    private JSObject pending = null;

    @Override
    public void load() {
        handleIntent(getActivity().getIntent());
    }

    @Override
    protected void handleOnNewIntent(Intent intent) {
        super.handleOnNewIntent(intent);
        handleIntent(intent);
    }

    private void handleIntent(Intent intent) {
        if (intent == null) return;
        String action = intent.getAction();
        if (!Intent.ACTION_SEND.equals(action) && !Intent.ACTION_SEND_MULTIPLE.equals(action)) return;
        final Intent it = intent;
        final String id = String.valueOf(System.currentTimeMillis());
        new Thread(() -> {
            JSObject data = new JSObject();
            data.put("id", id);
            data.put("type", it.getType() == null ? "" : it.getType());
            CharSequence text = it.getCharSequenceExtra(Intent.EXTRA_TEXT);
            data.put("text", text == null ? "" : text.toString());
            String subject = it.getStringExtra(Intent.EXTRA_SUBJECT);
            data.put("subject", subject == null ? "" : subject);
            JSArray images = new JSArray();
            for (Uri uri : collectUris(it)) {
                if (images.length() >= MAX_IMAGES) break;
                String img = readImage(uri);
                if (img != null) images.put(img);
            }
            data.put("images", images);
            synchronized (this) {
                pending = data;
            }
            notifyListeners("shared", data, true);
        }).start();
    }

    private List<Uri> collectUris(Intent it) {
        List<Uri> out = new ArrayList<>();
        try {
            if (Intent.ACTION_SEND_MULTIPLE.equals(it.getAction())) {
                ArrayList<Uri> list = IntentCompat.getParcelableArrayListExtra(it, Intent.EXTRA_STREAM, Uri.class);
                if (list != null) out.addAll(list);
            } else {
                Uri u = IntentCompat.getParcelableExtra(it, Intent.EXTRA_STREAM, Uri.class);
                if (u != null) out.add(u);
            }
            if (out.isEmpty()) {
                ClipData clip = it.getClipData();
                if (clip != null) {
                    for (int i = 0; i < clip.getItemCount(); i++) {
                        Uri u = clip.getItemAt(i).getUri();
                        if (u != null) out.add(u);
                    }
                }
            }
        } catch (Exception ignored) {
            // Contenu partagé illisible : on garde ce qu'on a.
        }
        List<Uri> images = new ArrayList<>();
        for (Uri u : out) {
            String mime = null;
            try {
                mime = getContext().getContentResolver().getType(u);
            } catch (Exception ignored) {
                // type inconnu : on tente quand même le décodage
            }
            if (mime == null || mime.startsWith("image/")) images.add(u);
        }
        return images;
    }

    private String readImage(Uri uri) {
        try {
            Bitmap bmp;
            if (Build.VERSION.SDK_INT >= 28) {
                ImageDecoder.Source src = ImageDecoder.createSource(getContext().getContentResolver(), uri);
                bmp = ImageDecoder.decodeBitmap(src, (decoder, info, source) -> {
                    decoder.setAllocator(ImageDecoder.ALLOCATOR_SOFTWARE);
                    int w = info.getSize().getWidth();
                    int h = info.getSize().getHeight();
                    int max = Math.max(w, h);
                    if (max > MAX_SIDE) {
                        float k = (float) MAX_SIDE / max;
                        decoder.setTargetSize(Math.max(1, Math.round(w * k)), Math.max(1, Math.round(h * k)));
                    }
                });
            } else {
                BitmapFactory.Options bounds = new BitmapFactory.Options();
                bounds.inJustDecodeBounds = true;
                try (InputStream in = getContext().getContentResolver().openInputStream(uri)) {
                    BitmapFactory.decodeStream(in, null, bounds);
                }
                int sample = 1;
                while (Math.max(bounds.outWidth, bounds.outHeight) / (sample * 2) >= MAX_SIDE) sample *= 2;
                BitmapFactory.Options opts = new BitmapFactory.Options();
                opts.inSampleSize = sample;
                try (InputStream in = getContext().getContentResolver().openInputStream(uri)) {
                    bmp = BitmapFactory.decodeStream(in, null, opts);
                }
            }
            if (bmp == null) return null;
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            bmp.compress(Bitmap.CompressFormat.JPEG, 85, out);
            bmp.recycle();
            return "data:image/jpeg;base64," + Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP);
        } catch (Exception | OutOfMemoryError e) {
            return null;
        }
    }

    @PluginMethod
    public void getPending(PluginCall call) {
        JSObject r = new JSObject();
        synchronized (this) {
            if (pending != null) r.put("data", pending);
        }
        call.resolve(r);
    }

    @PluginMethod
    public void clear(PluginCall call) {
        synchronized (this) {
            pending = null;
        }
        getActivity().runOnUiThread(() -> getActivity().setIntent(new Intent(Intent.ACTION_MAIN)));
        call.resolve();
    }
}
