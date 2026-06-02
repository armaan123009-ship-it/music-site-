'use client';

import React, { useEffect, useRef } from 'react';
import gsap from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import Lenis from '@studio-freight/lenis';
import { Mountain } from 'lucide-react';

export function ParallaxComponent() {
  const parallaxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    gsap.registerPlugin(ScrollTrigger);

    const triggerElement = parallaxRef.current?.querySelector('[data-parallax-layers]');

    if (triggerElement) {
      const tl = gsap.timeline({
        scrollTrigger: {
          trigger: triggerElement,
          start: "0% 0%",
          end: "100% 0%",
          scrub: 0
        }
      });

      const layers = [
        { layer: "1", yPercent: 40 },
        { layer: "2", yPercent: 20 },
        { layer: "3", yPercent: 10 },
        { layer: "4", yPercent: 0 }
      ];

      layers.forEach((layerObj, idx) => {
        tl.to(
          triggerElement.querySelectorAll(`[data-parallax-layer="${layerObj.layer}"]`),
          {
            yPercent: layerObj.yPercent,
            ease: "none"
          },
          idx === 0 ? undefined : "<"
        );
      });
    }

    const lenis = new Lenis({
      duration: 1.2,
      easing: (t) => Math.min(1, 1.001 - Math.pow(2, -10 * t)),
      orientation: 'vertical',
      gestureOrientation: 'vertical',
      smoothWheel: true,
      wheelMultiplier: 1,
      touchMultiplier: 2,
    });

    lenis.on('scroll', ScrollTrigger.update);
    gsap.ticker.add((time) => { lenis.raf(time * 1000); });
    gsap.ticker.lagSmoothing(0);

    return () => {
      ScrollTrigger.getAll().forEach(st => st.kill());
      if (triggerElement) gsap.killTweensOf(triggerElement);
      lenis.destroy();
    };
  }, []);

  return (
    <div className="relative w-full bg-black text-white" ref={parallaxRef}>
      {/* Header section with parallax */}
      <section className="relative h-[150vh] w-full overflow-hidden">
        <div className="sticky top-0 h-screen w-full overflow-hidden">
          <div data-parallax-layers className="relative h-full w-full">
            {/* Background Layer */}
            <img 
              src="https://images.unsplash.com/photo-1506905925346-21bda4d32df4?q=80&w=2000&auto=format&fit=crop" 
              loading="eager" 
              data-parallax-layer="1" 
              alt="Mountain Background" 
              className="absolute top-[-20%] left-0 w-full h-[140%] object-cover object-top opacity-70" 
            />
            
            {/* Midground Layer */}
            <img 
              src="https://images.unsplash.com/photo-1464822759023-fed622ff2c3b?q=80&w=2000&auto=format&fit=crop" 
              loading="eager" 
              data-parallax-layer="2" 
              alt="Mountain Midground" 
              className="absolute top-[-10%] left-0 w-full h-[120%] object-cover object-center opacity-80 mix-blend-lighten" 
            />
            
            {/* Text Layer */}
            <div data-parallax-layer="3" className="absolute inset-0 flex items-center justify-center z-10 pointer-events-none">
              <h2 className="text-[12vw] font-black uppercase tracking-tighter text-white drop-shadow-2xl">
                Parallax
              </h2>
            </div>
            
            {/* Foreground Layer */}
            <img 
              src="https://images.unsplash.com/photo-1454496522488-7a8e488e8606?q=80&w=2000&auto=format&fit=crop" 
              loading="eager" 
              data-parallax-layer="4" 
              alt="Mountain Foreground" 
              className="absolute bottom-[-10%] left-0 w-full h-[80%] object-cover object-bottom z-20 mask-image-b" 
            />
          </div>
          {/* Fade transition at the bottom */}
          <div className="absolute bottom-0 left-0 w-full h-48 bg-gradient-to-t from-black to-transparent z-30"></div>
        </div>
      </section>

      {/* Content section */}
      <section className="relative z-40 bg-black min-h-screen flex flex-col items-center justify-center p-8">
        <div className="max-w-3xl flex flex-col items-center text-center space-y-12">
          <div className="p-6 rounded-full bg-white/5 border border-white/10 backdrop-blur-md">
            <Mountain size={64} className="text-white" strokeWidth={1.5} />
          </div>
          <div className="space-y-6">
            <h3 className="text-4xl md:text-6xl font-bold tracking-tight">
              Modern Frontend <span className="text-transparent bg-clip-text bg-gradient-to-r from-gray-200 to-gray-500">Excellence</span>
            </h3>
            <p className="text-lg md:text-xl text-gray-400 leading-relaxed max-w-2xl mx-auto">
              Integrated with GSAP, Lenis, and Tailwind CSS to bring your scrolling experiences to life. Smooth, performant, and beautifully designed for the modern web.
            </p>
          </div>
        </div>
      </section>
    </div>
  );
}
